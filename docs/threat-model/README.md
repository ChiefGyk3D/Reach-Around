# Reach Around Threat Dragon model

`reach-around.threat-model.json` is the OWASP Threat Dragon 2.6.2 model for Reach Around. It translates the source threat model in `docs/security/threat-model.md` into four STRIDE data-flow diagrams. `RISKS.md` is the ranked plain-language view of the threats that remain Open.

The model covers the build and publish path, the visitor and owner request path, and the data stores, collectors, credentials, and private PDF and export path, and the local editor. A threat is Mitigated only when its control and a test are visible in this repository. Planned controls and controls that exist only in a provider account remain Open.

## Open the model

In the Threat Dragon desktop app, choose Open from the welcome page and select `docs/threat-model/reach-around.threat-model.json`. The file targets Threat Dragon 2.6.2.

For the web app, run Threat Dragon with local-file support, choose Open, and select the same file. The hosted demo may not have access to local repository files.

## Regenerate and maintain the diagrams

1. Treat `docs/security/threat-model.md` as the content source of truth.
2. Open the JSON in Threat Dragon and update cells, flows, threats, and positions.
3. Keep the four diagram titles and scopes stable. Add a cell or flow when a trust boundary, process, store, actor, or data path changes.
4. Put the matching T-id from `docs/security/threat-model.md` in every threat description and mitigation.
5. Set a cell's `hasOpenThreats` to true exactly when at least one threat on that cell has status Open.
6. Move a threat to Mitigated only when the mitigation names a repository path and the control has a test. Provider settings stay Open until independently verified and recorded.
7. Save from Threat Dragon, then validate:

Run a Threat Dragon v2 model validator against `docs/threat-model/reach-around.threat-model.json`. The one used here is the validator from the sibling no-php-no-cry project, run with `python3 -I` from the repository root. It checks the structure, ids, and the open-threat flags.

CI can run the same kind of check through the reusable `threat-model.yml` workflow in git-your-ship-together. It parses every file matching its `model-glob` input as a Threat Dragon v2 model and checks the threats. Its default glob is `docs/threat-model/*.threat-dragon.json`, so a caller here would set `model-glob: docs/threat-model/*.threat-model.json`. No caller is wired up yet.

Threat Dragon can regenerate rendered diagrams and reports from the model. Open the model, use its Report view, and print or export the selected diagrams. Do not treat a rendered report as the editable source.
