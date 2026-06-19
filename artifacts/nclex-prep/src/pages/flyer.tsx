import { QRCodeSVG } from "qrcode.react";

export default function Flyer() {
  return (
    <div className="min-h-screen bg-gray-100 flex items-center justify-center p-6 print:p-0 print:bg-white">
      <div
        className="w-[4.25in] bg-white rounded-2xl overflow-hidden shadow-2xl print:shadow-none print:rounded-none"
        style={{ fontFamily: "system-ui, sans-serif" }}
      >
        {/* Header */}
        <div className="bg-blue-600 px-6 py-5 text-white text-center">
          <div className="flex items-center justify-center gap-2 mb-1">
            <div className="w-8 h-8 bg-white rounded-lg flex items-center justify-center">
              <span className="text-blue-600 font-extrabold text-base">N</span>
            </div>
            <span className="font-extrabold text-2xl tracking-tight">NCLEX AI</span>
          </div>
          <p className="text-blue-200 text-xs font-semibold tracking-widest uppercase">nclexai.org</p>
        </div>

        {/* Headline */}
        <div className="px-6 pt-5 pb-3 text-center">
          <h1 className="text-[26px] font-extrabold text-gray-900 leading-tight mb-2">
            Pass the NCLEX on<br />
            <span className="text-blue-600">your first attempt</span>
          </h1>
          <p className="text-sm text-gray-500 leading-snug">
            AI explains why each wrong answer is wrong —<br />just like the real NCLEX tests your reasoning.
          </p>
        </div>

        {/* Stats bar */}
        <div className="mx-6 mb-4 bg-blue-50 border border-blue-100 rounded-xl px-4 py-3 grid grid-cols-4 text-center gap-2">
          {[
            { value: "2,778+", label: "Questions" },
            { value: "59", label: "Categories" },
            { value: "NGN", label: "Format" },
            { value: "CAT", label: "Adaptive" },
          ].map(({ value, label }) => (
            <div key={label}>
              <div className="text-base font-extrabold text-blue-700">{value}</div>
              <div className="text-[9px] text-gray-500 font-medium leading-tight">{label}</div>
            </div>
          ))}
        </div>

        {/* QR Code */}
        <div className="flex flex-col items-center pb-3 px-6">
          <div className="bg-gray-50 border border-gray-200 rounded-xl p-3 mb-2">
            <QRCodeSVG
              value="https://nclexai.org"
              size={150}
              bgColor="#f9fafb"
              fgColor="#1e3a8a"
              level="H"
            />
          </div>
          <p className="text-xs text-gray-400 text-center">Scan to start 10 free questions</p>
          <p className="text-sm font-bold text-blue-600 text-center mt-0.5">nclexai.org</p>
        </div>

        {/* Bullets */}
        <div className="px-6 pb-4 space-y-1.5">
          {[
            "✓  10 free questions — no credit card needed",
            "✓  AI explanations after every answer",
            "✓  NGN drag & drop question formats",
            "✓  59 nursing school categories",
            "✓  Created by a Registered Nurse",
            "✓  $20/mo or $100 one-time lifetime access",
          ].map((item) => (
            <p key={item} className="text-sm text-gray-700 font-medium">{item}</p>
          ))}
        </div>

        {/* Testimonial */}
        <div className="mx-6 mb-4 bg-gray-50 border border-gray-200 rounded-xl px-4 py-3">
          <div className="flex gap-0.5 mb-1">
            {[1,2,3,4,5].map(s => (
              <svg key={s} className="w-3 h-3 fill-yellow-400 text-yellow-400" viewBox="0 0 20 20">
                <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
              </svg>
            ))}
          </div>
          <p className="text-xs text-gray-600 italic leading-snug">"The questions looked identical to what I saw on test day. No other app comes close."</p>
          <p className="text-xs text-gray-400 font-semibold mt-1">— James T., BSN · Passed 1st attempt</p>
        </div>

        {/* Footer */}
        <div className="bg-blue-600 text-white text-center py-3 px-6">
          <p className="text-sm font-bold tracking-wide">nclexai.org · Free to start · No sign-up needed</p>
        </div>
      </div>

      {/* Print button */}
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
          .print\\:shadow-none { box-shadow: none !important; }
          .print\\:rounded-none { border-radius: 0 !important; }
          .print\\:p-0 { padding: 0 !important; }
          .print\\:bg-white { background-color: white !important; }
        }
      `}</style>
    </div>
  );
}
