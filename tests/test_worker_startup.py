"""No app bootstrap, actual database, threads or network during these checks."""
import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class WorkerStartupTests(unittest.TestCase):
    def test_module_import_does_not_start_watchers(self):
        tree = ast.parse((ROOT / 'app.py').read_text(encoding='utf-8'))
        calls = [node.value for node in tree.body if isinstance(node, ast.Expr)
                 and isinstance(node.value, ast.Call)]
        self.assertFalse(any(isinstance(c.func, ast.Name) and
                             c.func.id == 'start_background_watchers' for c in calls))
        fallback = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == 'ensure_background_watchers')
        self.assertTrue(any(isinstance(d, ast.Attribute) and d.attr == 'before_request'
                            for d in fallback.decorator_list))

    def test_gunicorn_worker_hook_owns_startup(self):
        spec = importlib.util.spec_from_file_location('isolated_gunicorn_config', ROOT / 'gunicorn.conf.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        start = Mock()
        worker = types.SimpleNamespace(pid=123, log=Mock())
        with patch.dict(sys.modules, {'app': types.SimpleNamespace(start_background_watchers=start)}):
            start.assert_not_called()
            module.post_worker_init(worker)
        start.assert_called_once_with()
        self.assertFalse(module.preload_app)
        with patch('gc.collect') as collect:
            module.pre_fork(None, None)
            collect.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
