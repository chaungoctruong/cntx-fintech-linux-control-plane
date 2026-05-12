# Release freeze — checklist (config guardrail)

**Tiếng Việt:** Checklist trước khi **tag release**, **đóng băng cấu hình**, hoặc **thêm server** control-plane — chỉ tick khi đã xác minh.

---

## A. Repo & git

- [ ] `git status` sạch **hoặc** chỉ còn thay đổi đã review (không lẫn file nhạy cảm).
- [ ] Thay đổi doc-only đã **tách commit** khỏi commit logic/runtime (khuyến nghị 1 commit chỉ docs + preflight).
- [ ] Tag release đã tạo: `git tag -a release/vX.Y.Z -m "..."` và `git push origin release/vX.Y.Z` (theo quy ước team).
- [ ] `git rev-parse HEAD` đã ghi vào [CONFIG_MANIFEST_TEMPLATE.md](CONFIG_MANIFEST_TEMPLATE.md).

---

## B. Env & secret hygiene

- [ ] File template / ví dụ trong repo **không** chứa token/password thật (chỉ placeholder).
- [ ] Secret chỉ nằm trong `.env` / vault — **không** commit.
- [ ] `chmod 600` trên mọi file env production.

---

## C. Preflight

- [ ] Đã chạy `bash ops/preflight_linux_control_plane.sh` trên server/staging tương đương prod.
- [ ] Kết quả: **0 FAIL** (WARN đã được ghi nhận và chấp nhận).

---

## D. Backup cấu hình

- [ ] Đã lưu backup (tarball hoặc export vault) gồm: env (encrypted), snippet nginx không secret, manifest đã điền.
- [ ] Vị trí backup đã ghi trong manifest field “Backup config location”.

---

## E. Health (sau khi được phép bật dịch vụ)

- [ ] `GET /ready` OK (local và/hoặc public).
- [ ] Hubbot: một consumer / token; log không `Conflict`.

---

## F. Rollback command (rõ ràng — không đụng DB)

Chỉ áp dụng cho **tài liệu + script preflight** của phase guardrail:

```bash
cd /path/to/linux-root-backend-hubot-v1
git checkout -- docs/CONFIG_MANIFEST_TEMPLATE.md docs/SCALE_NEW_SERVER_RUNBOOK.md docs/RELEASE_FREEZE_CHECKLIST.md ops/preflight_linux_control_plane.sh README.md
```

Hoặc quay tag:

```bash
git checkout <previous-release-tag>
```

**Không** dùng checklist này để `DROP DATABASE` / `FLUSHALL`.

---

## Sign-off

| Item | Owner | Date |
|------|-------|------|
| Preflight PASS | | |
| Manifest filed | | |
| Tag pushed | | |
