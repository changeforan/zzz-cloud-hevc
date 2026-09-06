# Public release checklist

The CI source-build pipeline succeeds (run `33966305836`). Its binaries with
the corrected Windows-to-Linux Steam registration were user-confirmed working
with ZZZ Cloud and Genshin Cloud. The registration fix and client confirmations
are committed at `ee93d90`. Compilation is not the outstanding release gate.

## Source and notices review

A bounded inspection of the CI archives found the following. This is engineering
release-readiness evidence, not a legal certification or a complete dependency
audit. Do not label the source snapshot complete merely because it is large.

| Component | Included evidence | Remaining work |
|---|---|---|
| Modified Wine | Actual source, generated `build/src-wine`, patches, recursive dependencies and build commands | Preserve the matching snapshot durably; make runner-specific rebuild instructions usable elsewhere. |
| Wine Mono 11.2.0 / Gecko 2.47.4 | Prebuilt archives in `proton/contrib`; upstream references in distribution licence | Obtain/provide the matching source under applicable component terms; inspect bundled dependencies and notices. |
| Xalia 0.4.9 | Prebuilt archive, not complete source | Preserve required notices and record exact source provenance; do not assume its requirements equal Wine's. |
| Piper dependency chain | Runtime libraries for eSpeak NG 1.52.0.1, ONNX Runtime 1.14.1 and piper-phonemize 1.2.0 | Recover matching inputs, especially copyleft eSpeak NG source; add dependency notices. `proton/piper/CMakeLists.txt` fetches a moving `pic` branch for phonemize, so version labels alone are insufficient. |
| Cargo dependencies | Lockfiles and build configuration; crate cache is outside the snapshot | Inventory shipped crates and satisfy their individual licence/notice requirements. |
| ICU | Prebuilt libraries; `proton/icu/README.md` identifies release-68-2 | Preserve ICU's licence/notice. Permissive licensing does not automatically require a source bundle. |
| OpenVR | Source and BSD licence already present | Preserve notices; do not treat this as an absent-source blocker. |

Keep the original LICENSE, LICENSE.OFL and other vendor notices, and supplement
missing coverage. A draft/prerelease designation does not waive distribution
requirements. The corrected local `11.0-2-hevc.4-r1` archive keeps the original
compiled binaries and source snapshot, with a separate archive of the committed
packaging recipe. It remains local pending the review above.

## ProtonPlus inclusion

Assuming “Proton hub” means ProtonPlus, its current contribution guide describes
definition-driven providers. After a distributable release is available:

1. Search existing requests and discuss adding Proton HEVC with the maintainers.
2. Add a provider in `src/models/providers/definitions/proton.vala` using the
   existing GitHub release source, with a stable ID and exact binary asset name.
3. Explicitly distinguish binary and source archives, and define install layout.
4. Add provider/registry/layout fixtures and run the documented tests.
5. Submit a PR against `main`. Inclusion is maintainer-reviewed, not automatic.

No published minimum release count or fixed waiting period was found in the
inspected guide. We have not submitted a provider request or PR.

References:
- https://github.com/Vysp3r/ProtonPlus/blob/main/CONTRIBUTING.md#adding-a-provider
- https://github.com/Vysp3r/ProtonPlus/blob/main/docs/provider-architecture.md
