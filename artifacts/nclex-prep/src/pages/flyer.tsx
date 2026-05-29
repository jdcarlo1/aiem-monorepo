import { QRCodeSVG } from "qrcode.react";

export default function Flyer() {
  return (
    <div className="min-h-screen bg-white flex items-center justify-center p-6 print:p-0">
      <div
        className="w-[4in] bg-white border-2 border-gray-200 rounded-2xl overflow-hidden shadow-xl print:shadow-none print:border-gray-300 print:rounded-none"
        style={{ fontFamily: "system-ui, sans-serif" }}
      >
        {/* Top color band */}
        <div className="bg-blue-600 px-6 py-5 text-white text-center">
          <div className="flex items-center justify-center gap-2 mb-1">
            <div className="w-7 h-7 bg-white rounded-md flex items-center justify-center">
              <span className="text-blue-600 font-extrabold text-sm">N</span>
            </div>
            <span className="font-extrabold text-xl tracking-tight">NCLEX AI</span>
          </div>
          <p className="text-blue-100 text-xs font-medium">nclexai.org</p>
        </div>

        {/* Headline */}
        <div className="px-6 pt-5 pb-3 text-center">
          <h1 className="text-2xl font-extrabold text-gray-900 leading-tight mb-2">
            Pass the NCLEX on<br />
            <span className="text-blue-600">your first attempt</span>
          </h1>
          <p className="text-sm text-gray-600 leading-snug">
            2,778+ AI-powered practice questions.<br />
            NGN format · 59 categories · Free to start.
          </p>
          <div className="mt-3 inline-flex items-center gap-1.5 bg-blue-50 border border-blue-200 text-blue-700 text-xs font-semibold px-3 py-1.5 rounded-full">
            ✅ Created by a Registered Nurse
          </div>
        </div>

        {/* QR Code */}
        <div className="flex flex-col items-center py-4 px-6">
          <div className="bg-gray-50 border border-gray-200 rounded-xl p-3 mb-2">
            <QRCodeSVG
              value="https://nclexai.org/start"
              size={160}
              bgColor="#f9fafb"
              fgColor="#1e3a8a"
              level="H"
            />
          </div>
          <p className="text-xs text-gray-500 text-center">Scan to start 10 free questions</p>
          <p className="text-sm font-bold text-blue-600 text-center mt-1">or visit: nclexai.org/start</p>
        </div>

        {/* Bullets */}
        <div className="px-6 pb-4 space-y-1.5">
          {[
            "✓  10 free questions — no credit card",
            "✓  AI explanations after every answer",
            "✓  NGN drag & drop question formats",
            "✓  59 nursing school categories",
            "✓  $15/mo or $49 lifetime access",
          ].map((item) => (
            <p key={item} className="text-sm text-gray-700 font-medium">{item}</p>
          ))}
        </div>

        {/* Footer band */}
        <div className="bg-blue-600 text-white text-center py-3 px-6">
          <p className="text-sm font-bold tracking-wide">nclexai.org/start</p>
        </div>
      </div>

      {/* Print button — hidden when printing */}
      <div className="fixed bottom-6 right-6 print:hidden">
        <button
          onClick={() => window.print()}
          className="bg-blue-600 hover:bg-blue-700 text-white font-bold px-6 py-3 rounded-xl shadow-lg text-sm"
        >
          🖨️ Print Flyer
        </button>
      </div>

      <style>{`
        @media print {
          body { margin: 0; }
          .print\\:hidden { display: none !important; }
        }
      `}</style>
    </div>
  );
}
