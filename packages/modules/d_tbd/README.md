# 模組 D — 尚未建立

這個資料夾是 S1 一次開好的佔位，負責人是 D。

負責人請在 S8 結束時照這個步驟建立自己的模組：

```bash
cp -r packages/modules/_template/* packages/modules/d_tbd/
```

然後改 `pack.yaml`（id 要跟資料夾名一致）、`playbook.yaml`、`module.py` 的類別名稱，
並把資料夾改名成 `d_<平台>_<手法>`。

規矩：這個資料夾只有你能改（見 `.github/CODEOWNERS`），別人動到會被機器人擋下來。
