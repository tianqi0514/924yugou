import { execFileSync } from 'node:child_process'
import path from 'node:path'
import { backendEnvironment, fixturePath, root } from './environment'

export default function globalSetup() {
  execFileSync(path.join(root, '.venv', 'bin', 'python'), [
    path.join(root, 'backend', 'scripts', 'setup_integration_qa.py'),
    '--fixture', fixturePath,
  ], {
    cwd: root,
    env: { ...process.env, ...backendEnvironment },
    stdio: 'pipe',
  })
}
