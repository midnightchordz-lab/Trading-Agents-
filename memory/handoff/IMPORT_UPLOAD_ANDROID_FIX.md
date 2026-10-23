# Portfolio import on Android: "Unsupported FormDataPart implementation" (branch `fix/import-upload`)

Builds on `feature/portfolio-import` @ `1bd496d`. Fetch and fast-forward:

```bash
cd /app
git fetch https://github.com/midnightchordz-lab/trading-agents-.git fix/import-upload
git merge --ff-only FETCH_HEAD
cd frontend && yarn install --frozen-lockfile   # should be a no-op, see below
```
Do not force-push, rebase or rewrite history. Save to GitHub from the UI.

## Cause

On a real Android build, "Import from Excel / CSV" fails with
"Couldn't import that file — Unsupported FormDataPart implementation".
Expo SDK 57 replaces the global `fetch` with `expo/fetch`
(`node_modules/expo/src/winter/runtime.native.ts`). Its FormData encoder
(`expo/src/winter/fetch/convertFormData.ts`) accepts only strings, Blobs,
or objects with `bytes()`. `api.portfolioImport` appended the classic React
Native `{ uri, name, type }` literal on native, which that encoder throws
on. Web never hit it because the picker gives a real browser `File` there.
This error comes from the app, not the backend: the request never left the phone.

## Fix

- `frontend/src/api.ts`: on native, append `new File(asset.uri)` from
  `expo-file-system` (implements Blob: `name`, `type`, `bytes()`). The picker
  already copies to cache (`copyToCacheDirectory: true`) as `<id>.<original
  ext>`, so the backend still sees `.xlsx` / `.csv`. Web path unchanged.
- `frontend/package.json`: `expo-file-system` `~57.0.6` declared explicitly.
  It was already installed as a dependency of `expo` at that exact range,
  and `yarn.lock` already has `expo-file-system@~57.0.6`, so the lockfile does
  not change. If `yarn install` wants to change it, stop and report.

Verified offline by compiling Expo's own `convertFormData.ts`: the old
literal reproduces "Unsupported FormDataPart implementation"; the File
produces a correct multipart part (`filename="<id>.xlsx"`, content-type,
bytes). `tsc` and eslint clean for `src/api.ts`.

## Verify

1. Web preview: import a small .xlsx and a .csv — still works (web path unchanged).
2. Needs a native build (Expo Go is fine for this, no native module added):
   on Android, Portfolio -> IMPORT FROM EXCEL / CSV -> pick an .xlsx
   (Name, Quantity, Avg Price) -> the review list appears. Repeat with a CSV
   and with a Zerodha holdings export.
3. Report the result and any error text verbatim.

No iOS build.
