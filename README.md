# Gemini Batch Image Generation Tool

Tạo ảnh stock / minh hoạ hàng loạt bằng **Gemini Batch API** (rẻ hơn 50% so với gọi thường). Mọi thứ chạy
trên máy bạn: một server Python nhỏ phục vụ dashboard HTML ở `http://127.0.0.1:8765`, không host ở đâu cả.

Tool này dành cho người **có AI assistant riêng** (Claude Code, Codex, Cursor, Gemini CLI...). AI đọc
`AGENTS.md` rồi lo việc: hỏi nhu cầu, viết prompt, chạy lệnh, theo dõi job. Bạn cần một Gemini API key.

## Mở dashboard

- **Bấm đúp `Open Dashboard.command`** trong thư mục này (Finder), hoặc
- gõ `./gbig open`, hoặc nhờ AI: *"mở dashboard"*.

Dashboard chạy nền, đóng Terminal vẫn chạy. Mở lại lúc nào cũng được (không lỗi nếu đang chạy).
Tắt: `./gbig stop`. Chưa có project thì trang sẽ hướng dẫn nên nói gì với AI.

## Bắt đầu

```bash
git clone https://github.com/nhanta0408/gemini-batch-image-generation-kit.git
cd gemini-batch-image-generation-kit
claude          # hoặc AI assistant bạn dùng, mở trong thư mục này
```

Rồi nói với AI, ví dụ: *"Tôi cần 20 ảnh văn phòng khoẻ mạnh cho blog, phụ nữ 25-50 tuổi."*
AI sẽ hỏi API key, chỗ lưu ảnh, rồi dẫn bạn qua từng bước.

Cần Python 3.9+ (macOS có sẵn; nếu máy báo về giấy phép Xcode: `sudo xcodebuild -license accept`).
Không cần cài thêm thư viện nào.

## Ảnh được lưu ở đâu

Không bao giờ nằm lẫn với source code:

- Mặc định: `output/<tên>/` trong thư mục này. Cả `output/` (project, lịch sử, log) đều nằm trong `.gitignore`.
- Hoặc thư mục bạn chọn: `./gbig init <tên> --dir ~/Pictures`. Đổi mặc định cho mọi project mới:
  `./gbig config --projects-dir <thư mục>`.

`git pull` bản mới của tool không đụng vào ảnh hay prompt của bạn.

## Dashboard có gì

- **Batch**: topic, tóm tắt, prompt đầy đủ (mở/đóng), khung kết quả (trống khi chưa có ảnh), approve / drop,
  OK / Redo, sửa prompt, nút **Generate with Gemini Batch** (hiện số ảnh + chi phí ước tính, xác nhận rồi mới
  gửi; nút chạy `gbig.py gen submit` thành một tiến trình Python riêng).
- **Jobs monitor**: trạng thái từng job, số ảnh, thời gian, chi phí. Tự kiểm tra mỗi phút và tải ảnh về;
  có Check now, Cancel, log.
- **Files & integrity**: so MD5 hiện tại với MD5 lúc Gemini trả ảnh: `ok`, `modified`, `renamed` (kèm tên
  mới), `missing`, `untracked`.
- Menu chọn project khi có nhiều project.

## Một project gồm gì

```
<project>/
  memory.md             ghi chú + bảng tự động: batch nào, job nào ra file nào, MD5, còn nguyên hay đã sửa
  project.json          tên, model / tỉ lệ khung mặc định
  batches/<batch>/
    batch.json          topic, style chung, model, tỉ lệ khung
    prompts.csv         danh sách ảnh (thứ duy nhất được sửa tay)
    manifest.json       nhật ký file do tool tạo (không sửa tay)
    images/             ảnh hiện tại; images/legacy/ là các bản vẽ trước (khi vẽ lại)
    jobs/               file làm việc của từng job Gemini Batch
```

## Lệnh

```bash
./gbig key set | check                        # lưu (vào .env, gitignored) / kiểm tra API key
./gbig init <name> [--dir <folder>]           # tạo project; ./gbig use <name> | projects | config
./gbig new <batch> --topic "..." [--style "..."] [--aspect 16:9]
./gbig import <batch> rows.json [--dry-run]
./gbig validate <batch> | prompts <batch>
./gbig gen submit <batch> [--ids a,b] [--yes]  # không có --yes = chỉ xem số ảnh + chi phí
./gbig gen wait [<batch>] [--once] | gen cancel <batch> <job>
./gbig open [<project>] | stop                # mở / tắt dashboard (chạy nền)
./gbig audit | status | memory
```

Phát triển tool: `docs/development.md`.
