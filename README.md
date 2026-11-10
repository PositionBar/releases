# PositionBar releases

PositionBar 的公开发布中心：发布 PR、版本与八语 changelog、Apple 签名并公证的 Universal DMG、Sparkle 更新源和官网发行记录。App 与官网源码仍在私有源码仓库；[Homebrew tap](https://github.com/PositionBar/homebrew-tap) 只消费同一份已验证安装包。

## 每次发版

1. 先将要发布的 App 改动按源码仓库的验收要求合入 `main`，固定完整 `source_sha`。
2. 从 `main` 建发布分支，只新增 `release-plans/<version>.json`。复制 [example](examples/release-plan.json)，填写真实版本、源码 SHA 与全部八种语言的 changelog；[JSON Schema](schemas/release-plan.schema.json) 可用于编辑器校验。示例不是发行记录。
3. 由 `sorcererxw-devbot` 创建 PR。CI 校验结构、私有 main 提交、版本递增，执行离线回归、真实 Updater XCTest 与无签名 Universal archive。
4. `sorcererxw` 审批当前 PR head，检查通过后 **Squash and merge**。main 的同一个 `release.yml` 自动构建发布；普通维护 PR 不发版，修改审批后的清单需另开更高版本 PR。

版本号由清单唯一决定，覆盖 Xcode 的开发默认值；Build ID 由全局 registry 分配，实际发布日期来自 GitHub Release。tag 自动生成 `positionbar-<version>-<build>`，固定到该发布 PR 的合并提交。源代码 SHA 不随 main 后续推进而改变；必须始终属于私有 main 的提交历史。

实际流程：验证 → 申请 Build ID 并保存原始清单 → Developer ID 签名 → App 公证/staple → Universal DMG 签名/公证/staple → 验证签名、架构和精确字节 → 发布 DMG、八语签名 notes 和签名 appcast → tap 重新下载校验并更新 cask → 上传发行索引 → 提升 latest 并公开读回 → 写入完成标记 `release.json`。全过程串行发布，不覆盖旧资产，不强推。

- [下载](https://github.com/PositionBar/releases/releases)
- Sparkle：`https://github.com/PositionBar/releases/releases/latest/download/appcast.xml`
- 官网发行索引：`https://github.com/PositionBar/releases/releases/latest/download/releases.json`
- 每个 tag 的 `release.json` 是完成记录，包含版本、Build ID、源 SHA、批准清单摘要、日期、八语内容和 DMG 摘要；计划文件本身不代表已经发布。

Sparkle / 官网从同一份清单生成八语内容，GitHub Release 使用其中的英文说明。官网服务端读取公开完成索引，不需要重新部署网站；历史无版本的官网日志保留原日期，不补造 App 版本。

## 配置

主分支要求 `Release plan`、`Verify direct-distribution source without publication` 检查及 owner Code Owner 审批，撤销过期审批，禁止直接推送/强推/删除，仅启用 squash merge。不要把真正的发布计划放入流水线安装 PR。

以下 Secrets 必须存在本仓库。GitHub 不允许读回已保存的明文；已有仓库的 Secrets 不会因新建仓库自动复制。

| Secret | 用途 |
| --- | --- |
| `GH_PAT` | 仅读私有 `sorcererxw/positionbar` 源码 |
| `TAP_WRITE_TOKEN` | 写 `PositionBar/homebrew-tap` 的 cask 与校验记录 |
| `DEVELOPER_ID_CERTIFICATE_P12_BASE64` | 已有 Developer ID Application 身份 |
| `DEVELOPER_ID_CERTIFICATE_PASSWORD` | 既有 P12 密码 |
| `APP_STORE_API_PRIVATE_KEY` | 既有 Apple API P8，仅作公证 |
| `SPARKLE_PRIVATE_KEY` | 与 App 已有公钥配对的更新签名密钥 |

Variables：`APP_STORE_API_KEY_ID`、`APP_STORE_API_ISSUER_ID`。Release 资产使用本仓库短期 `GITHUB_TOKEN`，不复用 tap 写入 token。签名前检查凭据是否缺失，避免无谓消耗 Build ID。App Store 分发证书和 provisioning profile 不参与。

Team `TX6HZR5RB7`、Bundle ID `com.positionbar.app`、Keychain service、已批准 entitlement 和 Sparkle 公钥保持不变。最低运行 macOS 26，安装包必须实际包含 arm64 与 x86_64；构建使用 GitHub-hosted `xcode-27`。Developer ID 证书可供用户查看，证书持有人姓名不会因自分发隐藏。

## 中断恢复

在 main 手动运行同一 `release.yml`，填写原 `release_pr` 与 `resume_run_id`。尚无公开资产的新 attempt 可以留空 run ID 并取得新 Build ID；一旦部分公开，必须恢复原 run。恢复必须匹配原始批准清单、源码、版本、Build ID 和已验证 DMG；只复用保留的原字节，不重新编译同一 Build ID。已有完成记录的重复触发直接结束。

`release-state` 在签名前保存身份和清单，`homebrew-state` 保存验证后的 DMG、签名 feed/notes 和非秘密 receipts，保留 30 天。不上传源码、P12/P8、Keychain、编译日志、dSYM 或中间 archive。

只有 identity、尚无验证 DMG 的失败轮次可以新建 attempt 获取新 Build ID；无 checkpoint 的 rerun 会拒绝执行。已经部分公开、却遗失原 DMG/checkpoint 时必须先人工核对，不得覆盖公开版本或假造完成状态。

本地验证：`python3 -B -m unittest discover -s tests -v`。离线测试不证明 Apple 公证、公开下载、真实安装升级或账户数据保留。
