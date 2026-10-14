# n8n templates for self-hosted AI

Free n8n workflows that keep an eye on the AI tools you host yourself: Ollama, Open WebUI, n8n, LiteLLM, vLLM, Langflow and Flowise. Each one runs on a schedule, checks your own setup against public data, and alerts you only when something needs attention.

| Workflow | What it watches | Alerts via | Keys needed |
|---|---|---|---|
| [Check self-hosted AI tools for known vulnerabilities](workflows/ai-tools-vulnerability-report.json) | The exact versions you run, against OSV.dev advisories, with the version to update to | Gmail | none |
| [Watch an Ollama server](workflows/ollama-server-watch.json) | Ollama version vs. latest release, advisories for that version, models you did not install | Telegram | none |
| [Audit Open WebUI](workflows/open-webui-admin-audit.json) | Open sign-up, default role, session expiry, API key scope, unexpected admins, new accounts, advisories | Slack | Open WebUI admin API key |

## Import

In n8n: **Workflows → Import from URL**, then paste the raw link, for example:

```
https://raw.githubusercontent.com/munimv2/n8n-templates/main/workflows/ai-tools-vulnerability-report.json
```

Or download the JSON file and use **Import from File**. Each workflow has a yellow note with full setup steps. Placeholders you must change start with `REPLACE_WITH_`.

## How the checks work

- **Advisories** come from the free [OSV.dev API](https://google.github.io/osv.dev/post-v1-query/). One request per tool asks which advisories affect that exact version, so there's no version-range parsing to get wrong. No key needed.
- **Your servers** are only read: version, model list, settings, user list. Nothing is changed.
- Some advisories for these tools are published only on the project's GitHub Security tab and never reach OSV.dev. MV2's [self-hosted AI security checklist](https://github.com/munimv2/self-hosted-ai-security-checklist) tracks the ones he found.

## Tests

Both run before every change is published. Python standard library only.

- `python test_workflows.py`: structure. Valid JSON, wired connections, descriptive node names, the description note, no stored credentials, balanced expressions, and every `$('Node name')` reference points at a real node.
- `python test_node_params.py`: every node parameter against the node definitions published in `n8n-nodes-base` (`dist/types/nodes.json`, the data the n8n editor builds its forms from): parameter names, option values, collection keys, and whether n8n shows the parameter for the chosen settings and node version. It downloads that file once (about 8 MB); set `SKIP_N8N_PARAMS=1` to skip it offline.

`tools/check-params.js` is a Node.js take on the same check that also calls n8n-workflow's parameter-issue function. Run it after `npm install n8n-nodes-base n8n-workflow` with `node tools/check-params.js workflows`.

## About

Made by MV2 of Munim, Inc. The templates were written with AI agents working in his name, then checked by the tests above. Issues and pull requests are welcome.

MIT licensed.
