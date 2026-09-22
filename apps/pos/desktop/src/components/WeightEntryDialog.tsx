import { useState } from "react";
import { WEIGHT_PRESETS_GRAMS, formatMoney, formatWeight } from "../lib/pricing";

/**
 * Phase 9A — weighted-product quantity entry. The CTO plan is explicit
 * that weight must never be "stored as a floating-point money-like value
 * without a defined unit model" — this dialog only ever produces a whole
 * number of GRAMS (an integer), matching OrderLine.quantity's documented
 * convention server-side. No physical scale exists in this environment
 * (electron/hardware/types.ts's ScaleProvider is an interface with no
 * implementation — disclosed, not faked here), so this is the manual
 * entry path: preset buttons for common weights plus a raw-grams field
 * for anything else, which is exactly what the CTO plan itself asks for
 * ("100g/250g/.../10kg and arbitrary scale quantity where supported").
 */
interface WeightEntryDialogProps {
  productName: string;
  pricePerKiloMinor: number;
  currency: string;
  onConfirm: (grams: number) => void;
  onCancel: () => void;
}

export function WeightEntryDialog({ productName, pricePerKiloMinor, currency, onConfirm, onCancel }: WeightEntryDialogProps) {
  const [grams, setGrams] = useState<number>(WEIGHT_PRESETS_GRAMS[0]);
  const [manualInput, setManualInput] = useState<string>("");

  const previewSubtotal = Math.round((pricePerKiloMinor * grams) / 1000);

  function applyManual() {
    const parsed = parseInt(manualInput, 10);
    if (Number.isFinite(parsed) && parsed > 0) {
      setGrams(parsed);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Enter weight for ${productName}`}
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
        if (e.key === "Enter") onConfirm(grams);
      }}
    >
      <div className="bg-white rounded p-4 w-96 space-y-3">
        <h2 className="font-semibold">{productName}</h2>
        <p className="text-xs text-gray-500">{formatMoney(pricePerKiloMinor, currency)} / kg — weighted item</p>

        <div className="grid grid-cols-4 gap-2">
          {WEIGHT_PRESETS_GRAMS.map((preset) => (
            <button
              key={preset}
              type="button"
              onClick={() => {
                setGrams(preset);
                setManualInput("");
              }}
              className={`border rounded py-2 text-xs ${grams === preset && !manualInput ? "bg-black text-white" : "hover:bg-gray-50"}`}
            >
              {formatWeight(preset)}
            </button>
          ))}
        </div>

        <div className="flex gap-2 items-center">
          <input
            type="number"
            min={1}
            step={1}
            placeholder="Custom grams"
            className="flex-1 border rounded px-2 py-1 text-sm"
            value={manualInput}
            onChange={(e) => setManualInput(e.target.value)}
            onBlur={applyManual}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                applyManual();
              }
            }}
          />
          <span className="text-xs text-gray-500">grams</span>
        </div>

        <div className="flex justify-between items-center text-sm font-medium border-t pt-2">
          <span>{formatWeight(grams)}</span>
          <span>{formatMoney(previewSubtotal, currency)}</span>
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" className="px-3 py-1.5 rounded border text-sm" onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className="px-3 py-1.5 rounded bg-black text-white text-sm"
            onClick={() => onConfirm(grams)}
            disabled={grams <= 0}
          >
            Add to Cart
          </button>
        </div>
      </div>
    </div>
  );
}
