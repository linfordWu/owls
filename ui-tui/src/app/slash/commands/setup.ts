import { withInkSuspended } from '@owls/ink'

import { launchOWLSCommand } from '../../../lib/externalCli.js'
import { runExternalSetup } from '../../setupHandoff.js'
import type { SlashCommand } from '../types.js'

export const setupCommands: SlashCommand[] = [
  {
    help: 'configure LLM provider + model (launches `owls model`)',
    name: 'provider',
    run: (_arg, ctx) =>
      void runExternalSetup({
        args: ['model'],
        ctx,
        done: 'provider updated — starting session…',
        launcher: launchOWLSCommand,
        suspend: withInkSuspended
      })
  },
  {
    help: 'run full setup wizard (launches `owls setup`)',
    name: 'setup',
    run: (arg, ctx) =>
      void runExternalSetup({
        args: ['setup', ...arg.split(/\s+/).filter(Boolean)],
        ctx,
        done: 'setup complete — starting session…',
        launcher: launchOWLSCommand,
        suspend: withInkSuspended
      })
  }
]
