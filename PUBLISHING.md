# HACS publication checklist

HACS default inclusion requires a public GitHub repository. The intended
repository is `Andresako/pi-hole-extended`; its URLs and code owner are already
set in the integration manifest.

## Before the first release

1. Set a repository description, enable issues, and add the topics
   `home-assistant`, `hacs`, `integration`, and `pihole`.
2. Push this project and confirm the HACS validation and Hassfest workflows pass.
3. Publish a GitHub Release whose tag matches the integration version in
   `manifest.json` (currently `0.1.0`).
4. Verify that the public repository can be added to HACS as a custom
   integration repository.

## Request default HACS inclusion

After the release and successful checks, the repository owner or a major
contributor can fork `hacs/default`, add the repository to its `integration`
list in alphabetical order on a new branch, and open an editable pull request
using HACS's current template. Default inclusion is reviewed by HACS and is not
instant. The software can be installed as a custom repository while that
request is pending.

This is inclusion in the HACS default catalog. It does not mean the integration
has been accepted into Home Assistant Core.
