"""In-memory agent protocol for preview journeys. No shell, model or external network."""
import json
import time
import httpx


class DemoRunner:
    def __init__(self):
        self.tasks = {}

    def handler(self, req):
        path = req.url.path.split('/')
        if req.url.path == '/tasks' and req.method == 'POST':
            data = json.loads(req.content)
            self.tasks[data['id']] = {'state': 'created', 'run': 0, 'events': [], 'changed': 0}
            return httpx.Response(200, json={'ok': True})
        tid = int(path[2])
        t = self.tasks.setdefault(tid, {'state': 'done', 'run': 1, 'events': [], 'changed': 0})
        action = path[3] if len(path) > 3 else ''
        if action in ('start', 'message'):
            t.update(state='running', started=time.time(), run=t['run'] + 1)
            t['events'].extend([
                {'type': 'text', 'run': t['run'], 'part': {'text': 'Demo simulation: inspecting the workspace. No commands are executed.'}},
                {'type': 'tool_use', 'run': t['run'], 'part': {'tool': 'read', 'state': {'status': 'completed', 'input': {'filePath': 'demo/index.html'}, 'output': 'Demo content'}}},
            ])
        elif action == 'cancel':
            t['state'] = 'cancelled'
        elif action == 'events':
            return httpx.Response(200, json={'events': t['events'][int(req.url.params.get('after', 0)):], 'state': t['state']})
        elif action in ('shots', 'summary', 'models', 'checkpoints'):
            return httpx.Response(200, json={'shots': [], 'files': [], 'models': [], 'checkpoints': []})
        elif action == 'diff':
            return httpx.Response(200, json={'diff': '', 'stat': ''})
        elif action == 'preview-info':
            return httpx.Response(200, json={'available': False, 'reason': 'This simulated task has no running project preview.'})
        elif not action:
            if t['state'] == 'running' and time.time() - t['started'] > 8:
                t['state'] = 'done'
                t['events'].append({'type': 'text', 'run': t['run'], 'part': {'text': 'Demo simulation finished. No files changed and no checks were run. STATUS: PARTIAL'}})
            return httpx.Response(200, json={**t, 'exit': 0, 'log': 'Simulated runner: no commands executed.', 'session': 'demo'})
        return httpx.Response(200, json={'ok': True})
