import unittest
from lab.training_entry import require_actual


class TrainingEntryTests(unittest.TestCase):
    def test_owned_placeholders_reject_but_real_preconfigured_entry_needs_no_patch(self):
        for command in ['python3 -c "print(\'IterLab CPU component ready\')"',
                        'python -c "print(\'IterLab approved component ready\');"',
                        'echo IterLab CPU component ready']:
            with self.subTest(command=command),self.assertRaisesRegex(ValueError,'占位测试命令'):
                require_actual(command)
        for command in ['python train.py','python "$ITERLAB_INPUT_DIR/implementation/launch.py"',
                        'python3 -c "print(\'custom diagnostic\')"',
                        'python3 -c "print(\'IterLab CPU component ready\'); train()"']:
            self.assertEqual(require_actual(command),command)
