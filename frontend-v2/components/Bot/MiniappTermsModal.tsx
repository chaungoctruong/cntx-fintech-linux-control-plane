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

export const MINIAPP_DISCLAIMER_NOTICE_PARAGRAPHS = [
  "CNTx Labs cung cấp công cụ công nghệ hỗ trợ vận hành bot, không cam kết lợi nhuận, không nhận ủy thác đầu tư và không thay mặt người dùng ra quyết định tài chính.",
  "Giao dịch tài chính luôn có rủi ro. Người dùng tự chịu trách nhiệm với tài khoản, vốn và quyết định giao dịch của mình.",
] as const;

function FullTermsContent() {
  return (
    <div className="space-y-5 text-sm leading-6 text-slate-200">
      <p>Vui lòng đọc kỹ trước khi kết nối tài khoản MT5 hoặc bật bot.</p>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">1. Vai trò của nền tảng</h3>
        <p>
          Nền tảng này chỉ cung cấp hạ tầng công nghệ, công cụ phần mềm và hệ thống hỗ trợ tự động hóa
          giao dịch trên tài khoản MT5 do người dùng tự sở hữu.
        </p>
        <p>
          Nền tảng không phải là tổ chức tư vấn đầu tư, không phải sàn giao dịch, không phải broker,
          không nhận ủy thác đầu tư, không giữ tiền của người dùng và không đại diện cho bất kỳ tổ chức
          tài chính nào.
        </p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">2. Không cam kết lợi nhuận</h3>
        <p>Bot giao dịch tự động không đảm bảo có lợi nhuận.</p>
        <p>
          Mọi kết quả mô phỏng, backtest, lịch sử giao dịch, hình ảnh lợi nhuận hoặc thông tin hiển thị
          trong hệ thống chỉ có giá trị tham khảo và không phải là cam kết lợi nhuận trong tương lai.
        </p>
        <p>Nền tảng không cam kết:</p>
        <ul className="list-disc space-y-1 pl-5">
          <li>Có lãi cố định;</li>
          <li>Không thua lỗ;</li>
          <li>Không cháy tài khoản;</li>
          <li>Bảo toàn vốn;</li>
          <li>Hoàn vốn;</li>
          <li>Đạt tỷ lệ thắng cụ thể;</li>
          <li>Đạt mức lợi nhuận theo ngày, tuần hoặc tháng.</li>
        </ul>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">3. Rủi ro giao dịch</h3>
        <p>
          Giao dịch Forex, CFD, vàng, tiền tệ, chỉ số hoặc các sản phẩm đòn bẩy có rủi ro cao. Người dùng
          có thể mất một phần hoặc toàn bộ số vốn trong tài khoản giao dịch.
        </p>
        <p>Người dùng tự chịu trách nhiệm đối với:</p>
        <ul className="list-disc space-y-1 pl-5">
          <li>Việc lựa chọn tài khoản giao dịch;</li>
          <li>Vốn nạp vào tài khoản;</li>
          <li>Đòn bẩy sử dụng;</li>
          <li>Khối lượng giao dịch;</li>
          <li>Việc bật/tắt bot;</li>
          <li>Kết quả lời/lỗ phát sinh;</li>
          <li>Mọi quyết định liên quan đến giao dịch.</li>
        </ul>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">4. Token và đối tác phân phối</h3>
        <p>
          Token truy cập bot có thể được cấp bởi đối tác phân phối/đơn vị giới thiệu. Đối tác phân phối
          chịu trách nhiệm độc lập đối với hoạt động tư vấn, chăm sóc khách hàng, thu phí, quảng cáo,
          giới thiệu sản phẩm và mọi cam kết riêng với người dùng nếu có.
        </p>
        <p>
          Nền tảng không chịu trách nhiệm đối với bất kỳ lời hứa, cam kết lợi nhuận, tư vấn đầu tư, kêu
          gọi góp vốn, nhận ủy thác hoặc nội dung quảng cáo nào do đối tác hoặc bên thứ ba đưa ra ngoài
          hệ thống chính thức.
        </p>
        <p>
          Nếu người dùng nhận được bất kỳ cam kết nào như “lãi chắc”, “không cháy”, “bảo toàn vốn”,
          “lợi nhuận cố định”, “được hoàn tiền nếu thua lỗ”, người dùng cần hiểu rằng các cam kết này
          không đến từ nền tảng và có thể là dấu hiệu rủi ro.
        </p>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">5. Không ủy thác, không huy động vốn</h3>
        <p>Người dùng không được sử dụng nền tảng để:</p>
        <ul className="list-disc space-y-1 pl-5">
          <li>Huy động vốn trái phép;</li>
          <li>Nhận tiền giao dịch thay người khác;</li>
          <li>Kêu gọi góp vốn;</li>
          <li>Cam kết lợi nhuận cho bên thứ ba;</li>
          <li>Bán tín hiệu hoặc bán bot bằng nội dung gây hiểu nhầm;</li>
          <li>Thực hiện hành vi lừa đảo, gian dối hoặc vi phạm pháp luật.</li>
        </ul>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">6. Quyền tạm khóa hoặc dừng dịch vụ</h3>
        <p>
          Nền tảng có quyền tạm khóa token, dừng bot, khóa tài khoản, hạn chế quyền truy cập hoặc chấm dứt
          cung cấp dịch vụ nếu phát hiện hoặc có căn cứ nghi ngờ người dùng/đối tác sử dụng hệ thống để:
        </p>
        <ul className="list-disc space-y-1 pl-5">
          <li>Cam kết lợi nhuận;</li>
          <li>Huy động vốn;</li>
          <li>Nhận ủy thác đầu tư;</li>
          <li>Lừa đảo;</li>
          <li>Quảng cáo sai sự thật;</li>
          <li>Gây hiểu nhầm cho khách hàng;</li>
          <li>Vi phạm pháp luật hoặc điều khoản sử dụng.</li>
        </ul>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-white">7. Xác nhận của người dùng</h3>
        <p>Bằng việc bấm “Tôi hiểu rủi ro và tiếp tục”, tôi xác nhận rằng:</p>
        <ul className="list-disc space-y-1 pl-5">
          <li>Tôi đã đọc, hiểu và đồng ý với toàn bộ điều khoản trên;</li>
          <li>Tôi hiểu bot không cam kết lợi nhuận;</li>
          <li>Tôi hiểu giao dịch có thể gây thua lỗ;</li>
          <li>Tôi tự chịu trách nhiệm với tài khoản MT5, vốn và kết quả giao dịch của mình;</li>
          <li>Tôi không giao tiền cho nền tảng để đầu tư hộ;</li>
          <li>Tôi không hiểu nền tảng là đơn vị bảo lãnh lợi nhuận hoặc bảo toàn vốn;</li>
          <li>Tôi đủ 18 tuổi và có đầy đủ năng lực chịu trách nhiệm đối với quyết định sử dụng dịch vụ.</li>
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
              "Tôi hiểu rủi ro và tiếp tục"
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
