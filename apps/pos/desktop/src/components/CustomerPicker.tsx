import { useState } from "react";

/**
 * Phase 9B — minimal customer attach: search an existing customer by
 * name/phone/email, or create one inline. Deliberately small — the
 * `customers` table (app/domain/customer.py) explicitly defers loyalty/
 * gift-card/store-credit to Phase 14, so this is just "find or create a
 * name+contact record and attach its id to the sale", nothing more.
 */
export interface CustomerDto {
  id: number;
  name: string;
  phone: string | null;
  email: string | null;
}

interface CustomerPickerProps {
  selected: CustomerDto | null;
  onSelect: (customer: CustomerDto | null) => void;
  onSearch: (q: string) => Promise<CustomerDto[]>;
  onCreate: (name: string, phone: string) => Promise<CustomerDto>;
}

export function CustomerPicker({ selected, onSelect, onSearch, onCreate }: CustomerPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CustomerDto[]>([]);
  const [newPhone, setNewPhone] = useState("");

  async function runSearch(q: string) {
    setQuery(q);
    if (q.length < 2) {
      setResults([]);
      return;
    }
    setResults(await onSearch(q));
  }

  if (selected) {
    return (
      <div className="flex items-center gap-2 text-sm border rounded px-2 py-1">
        <span>👤 {selected.name}</span>
        <button type="button" className="text-xs text-gray-500 underline" onClick={() => onSelect(null)}>
          Remove
        </button>
      </div>
    );
  }

  if (!open) {
    return (
      <button type="button" className="text-sm border rounded px-2 py-1 text-gray-600" onClick={() => setOpen(true)}>
        + Attach customer
      </button>
    );
  }

  return (
    <div className="border rounded p-2 text-sm space-y-1">
      <div className="flex gap-1">
        <input
          className="flex-1 border rounded px-1 py-0.5"
          placeholder="Search name, phone, email…"
          value={query}
          onChange={(e) => void runSearch(e.target.value)}
          autoFocus
        />
        <button type="button" className="text-xs text-gray-500" onClick={() => setOpen(false)}>
          ✕
        </button>
      </div>
      {results.map((c) => (
        <button
          key={c.id}
          type="button"
          className="block w-full text-left px-1 py-0.5 hover:bg-gray-50 rounded"
          onClick={() => {
            onSelect(c);
            setOpen(false);
          }}
        >
          {c.name} {c.phone ? `— ${c.phone}` : ""}
        </button>
      ))}
      {query.length >= 2 && results.length === 0 && (
        <div className="flex gap-1 items-center pt-1 border-t">
          <input
            className="flex-1 border rounded px-1 py-0.5"
            placeholder="New customer phone"
            value={newPhone}
            onChange={(e) => setNewPhone(e.target.value)}
          />
          <button
            type="button"
            className="text-xs bg-black text-white rounded px-2 py-0.5"
            onClick={async () => {
              const created = await onCreate(query, newPhone);
              onSelect(created);
              setOpen(false);
            }}
          >
            Create "{query}"
          </button>
        </div>
      )}
    </div>
  );
}
