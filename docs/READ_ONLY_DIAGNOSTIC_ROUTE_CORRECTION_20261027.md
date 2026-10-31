# Read-only diagnostic route correction — 2026-10-27

- The previous accessibility scan against port `9222` did not produce a diagnostic result and was not the right test for discovering the client interaction service.
- Correction to the earlier wording: the upstream setup guide does document Poi remote-debugging mode (`--remote-debugging-port=9222`) as a general prerequisite for the referenced automation setup. Therefore, `9222` is not necessarily irrelevant to the overall setup; it is simply **not the screenshot/input interaction service itself**.
- The documented Poi interaction route uses the matching forwarder plugin's interaction-server feature. The upstream setup guide says the plugin must be version `1.1.0` or newer for this service; older versions leave its control port closed. The referenced local interaction service defaults to `127.0.0.1:38591` and supports capture plus client-mediated input.
- Source: https://xvs32.github.io/kcauto_custom/Ch1/ ; protocol implementation was previously inspected in the upstream client source. Treat `38591` as the candidate to verify, not as proof that the user's installed service is running.
- The prior scan report was `FAILED` and contains no valid diagnostic result. It does not establish whether accessibility labels are available.
- Next step: read-only preflight of the existing plugin installation/configuration and local listener on the expected interaction port. Do not install plugins, change global settings, send game-server requests, or perform gameplay actions during preflight.
- MVP remains **NOT VERIFIED**. No live dispatch/claim/redispatch cycle is claimed.
