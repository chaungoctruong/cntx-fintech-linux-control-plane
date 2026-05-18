"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, ShieldCheck } from "lucide-react";

import type { AcceptMiniappTermsRequest } from "@/lib/api";

type MiniappTermsModalProps = {
  open: boolean;
  version: string;
  accepting?: boolean;
  error?: string | null;
  onAccept: (payload: AcceptMiniappTermsRequest) => void;
};

const checkboxItems = [
  {
    key: "checkbox_1",
    text: "Tôi hiểu bot không cam kết lợi nhuận, không bảo toàn vốn và có thể gây thua lỗ.",
  },
  {
    key: "checkbox_2",
    text: "Tôi xác nhận nền tảng chỉ cung cấp công nghệ, không nhận tiền đầu tư, không giữ tiền và không giao dịch thay tôi.",
  },
  {
    key: "checkbox_3",
    text: "Tôi hiểu mọi cam kết lợi nhuận từ đối tác/người giới thiệu/bên thứ ba không phải là cam kết của nền tảng.",
  },
] as const;

export const MINIAPP_RISK_WARNING_SHORT =
  "Cảnh báo: Bot giao dịch tự động không cam kết lợi nhuận. Giao dịch đòn bẩy có thể gây mất vốn. Nền tảng chỉ cung cấp công nghệ, không nhận ủy thác đầu tư, không giữ tiền và không chịu trách nhiệm đối với lời hứa/cam kết từ đối tác hoặc bên thứ ba.";

function FullTermsContent() {
  return (
    <div className="space-y-5 text-sm leading-6 text-slate-200">
      <p>Vui lòng đọc kỹ trước khi sử dụng nền tảng.</p>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">1. Vai trò của nền tảng</h3>
        <p>
          Nền tảng chỉ cung cấp phần mềm, hạ tầng kỹ thuật, server, dashboard, công cụ quản lý bot và
          công cụ hỗ trợ kết nối tài khoản giao dịch MT5.
        </p>
        <p>
          Nền tảng không phải là sàn giao dịch, không phải broker, không phải công ty chứng khoán, không
          phải công ty quản lý quỹ, không phải đơn vị nhận ủy thác đầu tư và không phải đơn vị cam kết
          lợi nhuận.
        </p>
        <p>
          Người dùng tự lựa chọn broker/sàn, tự mở tài khoản giao dịch, tự nạp/rút tiền và tự chịu
          trách nhiệm với tài khoản giao dịch của mình.
        </p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">2. Không tư vấn đầu tư</h3>
        <p>
          Các bot, tín hiệu, cấu hình, thông tin hiển thị, dữ liệu backtest, dữ liệu thử nghiệm hoặc nội
          dung từ đối tác/KOL chỉ nhằm mục đích cung cấp công cụ công nghệ và thông tin tham khảo.
        </p>
        <p>
          Nội dung trên nền tảng không được xem là lời khuyên đầu tư, khuyến nghị mua/bán, cam kết lợi
          nhuận hoặc bảo đảm kết quả giao dịch.
        </p>
        <p>
          Người dùng tự quyết định việc bật/tắt bot, lựa chọn bot, lựa chọn broker, cấu hình lot, SL,
          TP, rủi ro và mọi hành động giao dịch trên tài khoản của mình.
        </p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">3. Cảnh báo rủi ro giao dịch</h3>
        <p>
          Giao dịch tài chính, đặc biệt là forex, vàng, CFD, phái sinh, crypto hoặc các sản phẩm có đòn
          bẩy, có rủi ro rất cao.
        </p>
        <p>Người dùng có thể mất một phần hoặc toàn bộ số vốn trong tài khoản giao dịch.</p>
        <p>
          Hiệu suất quá khứ, kết quả backtest, kết quả demo hoặc kết quả thử nghiệm không đảm bảo kết
          quả trong tương lai.
        </p>
        <p>
          Nền tảng không cam kết lợi nhuận, không cam kết hoàn vốn, không cam kết tỷ lệ thắng và không
          chịu trách nhiệm cho khoản lỗ phát sinh từ quyết định sử dụng bot, tín hiệu hoặc cấu hình của
          người dùng.
        </p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">4. Quyền điều khiển bot</h3>
        <p>
          Người dùng có toàn quyền bật bot, tắt bot, thay đổi cấu hình, dừng sử dụng dịch vụ và quản lý
          tài khoản giao dịch của mình.
        </p>
        <p>
          Khi người dùng bật bot hoặc bật chế độ nhận tín hiệu, người dùng đồng ý cho hệ thống gửi lệnh
          kỹ thuật đến tài khoản MT5 theo cấu hình mà người dùng đã chọn hoặc đã xác nhận.
        </p>
        <p>
          Người dùng hiểu rằng bot có thể hoạt động sai, server có thể lỗi, broker có thể lỗi, MT5 có
          thể mất kết nối, thị trường có thể biến động mạnh và lệnh có thể bị trượt giá, từ chối hoặc
          khớp không như kỳ vọng.
        </p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">5. Quan hệ với broker/sàn</h3>
        <p>
          Nền tảng không giữ tiền của người dùng, không nhận tiền nạp/rút, không bảo quản tài sản giao
          dịch và không đại diện broker/sàn giao dịch.
        </p>
        <p>
          Mọi vấn đề liên quan đến tài khoản giao dịch, báo giá, đòn bẩy, khớp lệnh, spread, nạp/rút
          tiền, điều kiện giao dịch và tranh chấp với broker/sàn thuộc trách nhiệm giữa người dùng và
          broker/sàn mà người dùng đã lựa chọn.
        </p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">6. Phí dịch vụ</h3>
        <p>
          Người dùng có thể phải trả phí sử dụng phần mềm, bot, server, dashboard, hạ tầng hoặc các
          dịch vụ kỹ thuật khác theo gói đã chọn.
        </p>
        <p>
          Phí dịch vụ không phải là tiền đầu tư, không phải tiền ủy thác, không phải tiền nạp vào
          broker/sàn và không đảm bảo bất kỳ lợi nhuận nào.
        </p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">7. Dữ liệu và quyền riêng tư</h3>
        <p>
          Để cung cấp dịch vụ, nền tảng có thể thu thập và xử lý các dữ liệu cần thiết như: thông tin
          người dùng, tài khoản MT5, broker/server, trạng thái bot, cấu hình bot, lịch sử bật/tắt bot,
          tín hiệu, lệnh giao dịch, kết quả thực thi, log kỹ thuật, lỗi hệ thống và dữ liệu vận hành.
        </p>
        <p>
          Mật khẩu, token hoặc thông tin nhạy cảm sẽ được xử lý theo cơ chế bảo mật của hệ thống và
          không được cố ý hiển thị công khai.
        </p>
        <p>
          Nền tảng có thể sử dụng dữ liệu đã được ẩn danh hoặc giả danh để cải thiện hệ thống, nâng cấp
          bot, tối ưu hạ tầng, phân tích rủi ro, cải thiện tốc độ thực thi và phát triển sản phẩm.
        </p>
        <p>Nền tảng không bán dữ liệu cá nhân của người dùng cho bên thứ ba.</p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">8. Trách nhiệm của người dùng</h3>
        <p>Người dùng cam kết:</p>
        <ul className="list-disc space-y-1 pl-5">
          <li>Cung cấp thông tin chính xác.</li>
          <li>Tự chịu trách nhiệm với broker/sàn mình lựa chọn.</li>
          <li>Tự chịu trách nhiệm với cấu hình bot và mức rủi ro.</li>
          <li>Không sử dụng nền tảng cho mục đích gian lận, vi phạm pháp luật hoặc gây thiệt hại cho bên khác.</li>
          <li>Không chia sẻ tài khoản, mật khẩu hoặc quyền truy cập cho người không có thẩm quyền.</li>
        </ul>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">9. Giới hạn trách nhiệm</h3>
        <p>
          Trong phạm vi pháp luật cho phép, nền tảng không chịu trách nhiệm cho các khoản lỗ giao dịch,
          lỗi broker/sàn, lỗi kết nối Internet, lỗi MT5, lỗi server, lỗi dữ liệu, trượt giá, spread
          giãn, lệnh bị từ chối, biến động thị trường hoặc các thiệt hại gián tiếp phát sinh từ việc sử
          dụng dịch vụ.
        </p>
        <p>
          Nền tảng có thể tạm dừng, giới hạn hoặc ngắt dịch vụ khi phát hiện rủi ro kỹ thuật, hành vi
          bất thường, lỗi hệ thống, vi phạm điều khoản hoặc sự kiện bất khả kháng.
        </p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">10. Xác nhận đồng ý</h3>
        <p>Bằng việc bấm “Tôi đồng ý”, người dùng xác nhận rằng:</p>
        <ul className="list-disc space-y-1 pl-5">
          <li>Đã đọc và hiểu điều khoản sử dụng.</li>
          <li>Đã hiểu rủi ro giao dịch và khả năng mất tiền.</li>
          <li>Hiểu rằng nền tảng không cam kết lợi nhuận.</li>
          <li>Hiểu rằng nền tảng không phải broker, không phải quỹ và không nhận ủy thác đầu tư.</li>
          <li>Đồng ý cho hệ thống xử lý dữ liệu cần thiết để cung cấp dịch vụ.</li>
          <li>Tự chịu trách nhiệm với mọi quyết định sử dụng bot, tín hiệu, broker/sàn và tài khoản giao dịch của mình.</li>
        </ul>
      </section>
    </div>
  );
}

export default function MiniappTermsModal({
  open,
  version,
  accepting = false,
  error,
  onAccept,
}: MiniappTermsModalProps) {
  const [expanded, setExpanded] = useState(false);
  const [checks, setChecks] = useState({
    checkbox_1: false,
    checkbox_2: false,
    checkbox_3: false,
  });

  const allChecked = useMemo(() => Object.values(checks).every(Boolean), [checks]);

  if (!open) {
    return null;
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="miniapp-terms-title"
      className="fixed inset-0 z-[80] flex min-h-[100dvh] items-end justify-center bg-black/70 px-3 py-3 backdrop-blur-md sm:items-center"
    >
      <div className="flex max-h-[92dvh] w-full max-w-md flex-col overflow-hidden rounded-t-[28px] border border-cyan-300/20 bg-[#071018] shadow-2xl shadow-black/40 sm:rounded-[28px]">
        <div className="border-b border-white/10 bg-white/[0.03] px-5 py-4">
          <div className="flex items-start gap-3">
            <div className="rounded-2xl border border-amber-300/25 bg-amber-300/10 p-2 text-amber-100">
              <ShieldCheck className="h-5 w-5" strokeWidth={1.9} />
            </div>
            <div className="min-w-0">
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-cyan-100">
                CNTx labs
              </p>
              <h2 id="miniapp-terms-title" className="mt-1 text-xl font-semibold text-white">
                Điều khoản sử dụng & Cảnh báo rủi ro
              </h2>
            </div>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
          <div className="rounded-2xl border border-amber-300/20 bg-amber-300/10 px-4 py-3 text-sm leading-6 text-amber-50">
            <div className="flex gap-2">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" strokeWidth={1.9} />
              <p>
                Bot giao dịch tự động có rủi ro thua lỗ. Nền tảng chỉ cung cấp công nghệ, không nhận ủy
                thác đầu tư, không giữ tiền và không chịu trách nhiệm đối với cam kết từ đối tác hoặc bên
                thứ ba.
              </p>
            </div>
          </div>

          <div className="mt-4 grid gap-3 text-sm leading-6 text-slate-200">
            <div className="flex gap-2">
              <CheckCircle2 className="mt-1 h-4 w-4 shrink-0 text-cyan-100" strokeWidth={1.9} />
              <p>Bot không đảm bảo có lợi nhuận và kết quả quá khứ chỉ có giá trị tham khảo.</p>
            </div>
            <div className="flex gap-2">
              <CheckCircle2 className="mt-1 h-4 w-4 shrink-0 text-cyan-100" strokeWidth={1.9} />
              <p>User tự quyết định tài khoản, vốn, đòn bẩy, cấu hình bot và chịu kết quả lời/lỗ.</p>
            </div>
            <div className="flex gap-2">
              <CheckCircle2 className="mt-1 h-4 w-4 shrink-0 text-cyan-100" strokeWidth={1.9} />
              <p>Token đối tác không làm phát sinh cam kết lợi nhuận từ nền tảng.</p>
            </div>
          </div>

          <button
            type="button"
            onClick={() => setExpanded((current) => !current)}
            className="mt-4 min-h-[42px] rounded-2xl border border-cyan-300/25 bg-cyan-300/10 px-4 py-2 text-sm font-semibold text-cyan-50 transition hover:border-cyan-300/40 hover:bg-cyan-300/15"
          >
            {expanded ? "Ẩn đầy đủ điều khoản" : "Xem đầy đủ điều khoản"}
          </button>

          {expanded && (
            <div className="mt-4 rounded-2xl border border-white/10 bg-black/20 px-4 py-4">
              <FullTermsContent />
            </div>
          )}

          <div className="mt-4 grid gap-3">
            {checkboxItems.map((item) => (
              <label
                key={item.key}
                className="flex gap-3 rounded-2xl border border-white/10 bg-white/[0.035] px-4 py-3 text-sm leading-6 text-slate-100"
              >
                <input
                  type="checkbox"
                  checked={checks[item.key]}
                  onChange={(event) =>
                    setChecks((current) => ({
                      ...current,
                      [item.key]: event.target.checked,
                    }))
                  }
                  className="mt-1 h-4 w-4 shrink-0 accent-cyan-300"
                />
                <span>{item.text}</span>
              </label>
            ))}
          </div>

          {error && (
            <div className="mt-4 rounded-2xl border border-rose-300/25 bg-rose-300/10 px-4 py-3 text-sm leading-6 text-rose-100">
              {error}
            </div>
          )}
        </div>

        <div className="border-t border-white/10 bg-[#071018]/95 px-5 py-4">
          <button
            type="button"
            disabled={!allChecked || accepting}
            onClick={() =>
              onAccept({
                version,
                checkbox_1: checks.checkbox_1,
                checkbox_2: checks.checkbox_2,
                checkbox_3: checks.checkbox_3,
              })
            }
            className="flex min-h-[50px] w-full items-center justify-center gap-2 rounded-2xl border border-cyan-300/30 bg-cyan-300/15 px-4 py-3 text-sm font-semibold text-cyan-50 transition hover:border-cyan-300/45 hover:bg-cyan-300/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {accepting ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" strokeWidth={1.9} />
                Đang lưu xác nhận
              </>
            ) : (
              "Tôi đồng ý"
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
