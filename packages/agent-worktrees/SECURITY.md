# Security policy

## Reporting

Use GitHub private vulnerability reporting. Do not attach real credentials, customer data, proprietary prompts, private source, or production logs.

## Trust boundary

Agent Worktrees is a local Git coordination reference, not a sandbox or identity provider. It deliberately avoids remotes and shells, but it operates with the filesystem and Git permissions of its caller.

Production adopters should:

- authenticate and authorize every register, transition, complete, and cleanup caller;
- place the SQLite file and worktree root under restrictive filesystem permissions;
- sign or independently verify commits, test results, and artifacts;
- back up repositories and state before large orchestration runs;
- rate-limit registrations, retries, recovery, and audit operations;
- review Git hooks and repository configuration as executable local trust inputs;
- keep normal protected-branch and pull-request controls around integration;
- never expose arbitrary repository paths from untrusted tenants to the same process.

The CLI bounds JSON inputs to 1 MB and Git commands to 30 seconds. These limits do not sandbox Git hooks or repository contents.
