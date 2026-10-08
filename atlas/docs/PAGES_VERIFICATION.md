# Atlas GitHub Pages hosting verification

Updated: 2026-10-07. Hosting-only extension of the verified V1 workspace. All local verification gates passed. **Source publication and website deployment COMPLETE.** Public HTTPS HTML/resources returned200; remote iPhone WebKit functional smoke passed. Physical-device UAT remains pending.

## Scope

Same V1 engines and IndexedDB schema. Separate static project-path build, scoped PWA, explicit Yahoo/SEC unavailability, and free GitHub Pages workflow. No new research features, cloned workspace, extracted archives or Git initialization.

## Gates

Passed: clean `npm ci`; **136/136 unit tests (18 files)**; lint both before and after generated Pages output; TypeScript/normal production build; separate Pages build; **8/8 isolated Pages production browser cases** (40.3 seconds). The existing V1 browser regression also passed: **78 PASS / 10 expected SKIP / 0 FAIL (88 cases, 8.1 minutes)**. No Phase1/2/V1 regression was found. Skips retain the established desktop-only stress and touch-only applicability; no mobile coverage was reduced.

Pages profiles: Desktop Chromium2PASS, Mobile Chromium2PASS, iPad WebKit2PASS, iPhone WebKit2PASS. Coverage includes subpath assets/icons/manifest, registration scope, preserving another app's cache while deleting only Atlas's stale scoped cache, touch-mode drawing and locks, symbol-owned SMA isolation/reload, portable Yahoo-provider backup and manual Demo recovery with zero backend requests, and offline Demo/SMA reopening. WebKit's forced-offline mode prevents worker dispatch, so its offline tests stop an isolated real preview server after caching. No physical Safari/FPS claim.

Build warnings are the existing third-party Zod Rollup annotation notices; lint is clean.

## Review

Plan and read-only implementation review: GPT-6.1 Sol High. Frozen implementation: GPT-6 Luna Max. Primary triage accepts removal of the unplanned Yahoo import rejection and adding `dist-pages/**` to ESLint ignores. Static Yahoo cache bypass is accepted to make backend absence explicit; normal cache behavior stays unchanged. Gemini/Claude independent review remains unavailable; REVIEW_PACKAGE.md records the handoff.

## Historical source publication / Pages owner setup checkpoint (resolved below)

Target: `wudn9922/lightweight-drawing-lab`, source published to main in commit5f87026e5a5983bc6199d4ffb103fd89c2f018a4. User authorized installation168757214 onwudn9922. Contents/workflows/actions writes succeed. Intended website: `https://wudn9922.github.io/lightweight-drawing-lab/`; it is not yet live. The remaining blocker is Pages owner setup, not repository contents authorization: POSTPages returns403, and hosted configure-pages fails404 until Source is set to GitHub Actions.

Combined browser evidence: **86 passed / 10 expected skipped** across normal V1 and static project-path suites. All four profiles passed both suites.

## Historical authorization blocker — resolved by user authorization

- CLI PUT repository contents/README.md: HTTP403, `Resource not accessible by integration`.
- Connected GitHub create_file action: sameHTTP403 / FORBIDDEN.
- POST repository Pages with build_type=workflow: sameHTTP403.
- Target branches remainempty afterattempts. No source was pushed or deployed.
- Intended public site actually requested overHTTPS: HTTP404. It is not a working testURL.
- Available App installation152596461 belongs to saku0827, repositoryselectionall but its accessible list is only saku0827/fast-launch-api; targetowner wudn9922 is not covered. App contents/workflows/actions writepermissions on its owninstallation do not authorize this otherrepository.

User action required: in ChatGPT's GitHubconnection settings/manageinstallation, install/authorize **ChatGPT Codex Connector** for wudn9922/lightweight-drawing-lab. Do not paste tokens/credentials into chat. After authorization, continue API publication from this workspace; rereadremote branches beforewrite. Pages may additionally require its owner to choose Settings→Pages→GitHubActions because currentApp permission metadata has no Pages/administration grant. Preparedsource/workflow/testresults remainready.

## Historical authorization retry — succeeded

After the user confirmed authorization, GitHub installation168757214 onwudn9922 was visible and its repository list includedlightweight-drawing-lab. Targetbranchlist remainedempty. Contents publication subsequently succeeded; actual commit/Pages results are recorded in the following section.

## Historical source published / Pages setup pending (resolved below)

Successful source commit: [5f87026](https://github.com/wudn9922/lightweight-drawing-lab/commit/5f87026e5a5983bc6199d4ffb103fd89c2f018a4),172files onmain. Contents/workflow authorization issue isresolved.

[GitHubActions run37580250183](https://github.com/wudn9922/lightweight-drawing-lab/actions/runs/37580250183) passed npmci/unit136/lint/build:pages onthehostedrunner, thenfailed only atconfigure-pages because PagesGET returned404. Deployment wasskipped. Explicit POSTPages withbuild_typeworkflow returned403 `Resource not accessible by integration`.

Official actions/configure-pages/action.yml states automaticenablement requires a token otherthanGITHUB_TOKEN, and Appadministration:write/pages:write grants. CurrentAppdoesnot have thosegrants. No secret/PATisneededfornormalActionsdeployment once theowner selectsSettings→Pages→SourceGitHubActions. Userwasgiven that exactsetupURL/action. Afterenablement, rerunworkflow andcheckactualHTTPSHTML/JS/icons/manifest/worker.

## Verified live deployment

Owner enabled SourceGitHubActions; [run37596025627](https://github.com/wudn9922/lightweight-drawing-lab/actions/runs/37596025627) completed build anddeploySUCCESS for mainf29994ae92997c52430229d55b2da6b49ca8dcf0. Hostednpmci/test136/lint/build/configure/artifact/deploy allPASS.

**Verified URL: https://wudn9922.github.io/lightweight-drawing-lab/**

HTML200, allreferencedJS/CSS/icons/manifest/serviceworker200, correctprojectbase. Exactpublicresource hashes in[HTTPevidence](pages-live-http.json). RemoteiPhoneWebKitPASS: DemoOHLC, SMA24lock/reload, unavailableSECpanel, correctPWAregistrationscope, zeroAPIrequests andruntimeerrors; TLSverificationenabled. [Browserevidence](pages-live-browser-smoke.json). ThisisheadlessWebKit, notphysicaliPhoneSafari.

RemoteChromium navigation hitmanagedproxyCAtrusterror beforeUI. AutomaticreviewrejectedchangingglobalChromiumNSStrustbecauseitpersistentlychangesfuturetrustboundaries; nochangeexecuted andTLSverificationwasnotdisabled. HTTPS/curl+remoteWebKitpassed; existinglocalfourprofilePagesregression8PASS isunchanged. Thisenvironment-onlyremoteChromiumsmoke isBLOCKED, notapplicationPASS.

Sourcecode/coreengines unchangedaftertestedpublication; latestdocumentationcommit recordsdeployment. Futuremaincommitsautodeploy. Yahoo/SEC remainexplicitlybackend-required inPages; no fakefinancials.
