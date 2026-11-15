"""Evaluate the actual HA blueprint templates against success/failure histories."""
from pathlib import Path
from types import SimpleNamespace
import unittest

import yaml
from jinja2 import Environment, StrictUndefined


class BlueprintLoader(yaml.SafeLoader):
    pass


BlueprintLoader.add_constructor('!input', lambda loader, node: loader.construct_scalar(node))
ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT = yaml.load((ROOT / 'ezmart_deco_optimizer/examples/camera_until_confirmed.yaml').read_text(), Loader=BlueprintLoader)
BUSY = 'binary_sensor.ezmart_deco_em_execucao'
READY = 'binary_sensor.ezmart_deco_pronto_para_otimizar'
RESULT = 'sensor.ezmart_deco_resultado'
REQUEST = 'sensor.ezmart_deco_ultimo_pedido'
BLOCK = 'sensor.ezmart_deco_bloqueio'


class RetryAutomationTests(unittest.TestCase):
    def setUp(self):
        self.states = {BUSY: 'off', READY: 'on', RESULT: 'acknowledged_with_channel_change',
                       REQUEST: 'apply_acknowledged', BLOCK: 'ready'}
        self.attributes = {'request_id': 'ha-new', 'last_request_id': 'ha-new',
                           'verification': 'stable', 'api_code': 0}
        self.context = {'relatorio_anterior': 'm9-old', 'pedido_anterior': 'ha-old'}
        self.env = Environment(undefined=StrictUndefined)
        self.env.globals.update(states=lambda entity: self.states.get(entity, 'unknown'),
                                is_state=lambda entity, value: self.states.get(entity) == value,
                                state_attr=lambda entity, key: self.attributes.get(key) if entity == RESULT else None)
        self.sequence = BLUEPRINT['actions'][2]['repeat']['sequence']

    def render(self, template, **context):
        return self.env.from_string(template).render(**(self.context | context)).strip() == 'True'

    def confirmed(self):
        # Both exits must agree, including a completion observed after a timeout.
        exits = [step['if'][0]['value_template'] for step in self.sequence if 'if' in step]
        result = [self.render(template) for template in exits]
        self.assertEqual(result, [result[0]] * 2)
        return result[0]

    def test_fresh_ack_and_stable_observation_confirm_with_or_without_channel_change(self):
        for conclusion in ('acknowledged_with_channel_change', 'acknowledged_without_observed_change'):
            for request in ('apply_acknowledged', 'verification_only'):
                with self.subTest(conclusion=conclusion, request=request):
                    self.states.update({RESULT: conclusion, REQUEST: request})
                    self.assertTrue(self.confirmed())

    def test_historical_or_missing_ids_never_confirm(self):
        for old in ('m9-old', 'ha-old', '', None):
            with self.subTest(old=old):
                self.attributes.update(request_id=old, last_request_id=old)
                self.assertFalse(self.confirmed())

    def test_report_must_match_latest_request(self):
        self.attributes['last_request_id'] = 'different-request'
        self.assertFalse(self.confirmed())

    def test_old_success_cannot_hide_rejection_timeout_or_missing_ack(self):
        for state in ('optimization_not_needed', 'apply_rejected', 'request_cooldown',
                      'action_failed_no_replay', 'request_expired', 'accepted', 'running',
                      'interrupted_no_replay', 'unknown', 'unavailable'):
            with self.subTest(state=state):
                self.states[REQUEST] = state
                self.assertFalse(self.confirmed())

    def test_uncertain_or_incomplete_results_never_confirm(self):
        for result in ('unconfirmed_with_channel_change', 'unconfirmed_without_observed_change',
                       'optimization_not_needed', 'apply_outcome_uncertain', 'unknown', 'unavailable'):
            with self.subTest(result=result):
                self.states[RESULT] = result
                self.assertFalse(self.confirmed())

    def test_stable_channels_alone_do_not_prove_acceptance(self):
        for code in (None, 1, -4104, '0'):
            with self.subTest(code=code):
                self.attributes['api_code'] = code
                self.assertFalse(self.confirmed())

    def test_acceptance_alone_does_not_prove_completed_verification(self):
        for verification in (None, 'timeout', 'incomplete', 'unavailable'):
            with self.subTest(verification=verification):
                self.attributes['verification'] = verification
                self.assertFalse(self.confirmed())

    def test_busy_or_unavailable_never_allows_success_or_new_dispatch(self):
        for busy in ('on', 'unknown', 'unavailable'):
            with self.subTest(busy=busy):
                self.states[BUSY] = busy
                self.assertFalse(self.confirmed())
                self.assertFalse(self.render(self.sequence[0]['wait_template']))

    def test_pending_verification_can_progress_but_other_blocks_wait(self):
        self.states[READY] = 'off'
        for block in ('commands_disabled', 'request_cooldown', 'tmp_auth_cooldown', 'monitor_unavailable'):
            self.states[BLOCK] = block
            self.assertFalse(self.render(self.sequence[0]['wait_template']))
        self.states[BLOCK] = 'previous_apply_unverified'
        self.states['button.ezmart_deco_verificar'] = 'unknown'  # Valid unpressed button.
        self.assertTrue(self.render(self.sequence[0]['wait_template']))
        self.states['button.ezmart_deco_verificar'] = 'unavailable'
        self.assertFalse(self.render(self.sequence[0]['wait_template']))

    def test_startup_unknown_does_not_create_a_camera_failure(self):
        template = BLUEPRINT['conditions'][0]['value_template']
        for state in (None, 'unknown', 'unavailable', 'idle', 'streaming'):
            trigger = SimpleNamespace(from_state=SimpleNamespace(state=state) if state else None)
            self.assertEqual(self.render(template, trigger=trigger), state in ('idle', 'streaming'))

    def test_retry_flow_has_no_unconditional_success_and_preserves_backoff(self):
        self.assertEqual(BLUEPRINT['mode'], 'single')
        self.assertFalse(self.render(BLUEPRINT['actions'][2]['repeat']['until'][0]['value_template']))
        self.assertEqual(self.sequence[-1]['delay'], '00:15:00')
        waits = [s for s in self.sequence if 'timeout' in s]
        self.assertTrue(all(s['continue_on_timeout'] for s in waits))
        # No camera-recovery or ready condition may silently end an admitted run.
        self.assertTrue(all('condition' not in s for s in self.sequence))
        choice = next(s for s in self.sequence if 'choose' in s)
        self.assertEqual(choice['choose'][0]['sequence'][0]['target']['entity_id'], 'button.ezmart_deco_verificar')
        self.assertEqual(choice['default'][0]['target']['entity_id'], 'button.ezmart_deco_otimizar')
        for step in (choice['choose'][0]['sequence'][0], choice['default'][0]):
            self.assertTrue(step['continue_on_error'])


if __name__ == '__main__':
    unittest.main()
