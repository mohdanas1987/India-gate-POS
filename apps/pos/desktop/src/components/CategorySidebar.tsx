/**
 * Phase 9A — category navigation. Previously NOT FOUND anywhere in the
 * POS (the CTO's gap analysis confirmed no category filter UI existed at
 * all). Backed by GET /api/v1/categories online and the local
 * `local_categories` snapshot offline (see electron/sync/schema.ts), so
 * this same component works identically in both states.
 */
export interface CategoryDto {
  id: number;
  name: string;
  slug: string;
  product_count: number;
}

interface CategorySidebarProps {
  categories: CategoryDto[];
  selectedCategoryId: number | null;
  onSelect: (categoryId: number | null) => void;
}

export function CategorySidebar({ categories, selectedCategoryId, onSelect }: CategorySidebarProps) {
  const totalCount = categories.reduce((sum, c) => sum + c.product_count, 0);

  return (
    <nav aria-label="Product categories" className="w-40 shrink-0 border-r overflow-y-auto">
      <button
        type="button"
        onClick={() => onSelect(null)}
        className={`w-full text-left px-3 py-2 text-sm border-b flex justify-between items-center ${
          selectedCategoryId === null ? "bg-black text-white" : "hover:bg-gray-50"
        }`}
      >
        <span>All Products</span>
        <span className="text-xs opacity-70">{totalCount}</span>
      </button>
      {categories.length === 0 && (
        <p className="px-3 py-4 text-xs text-gray-400">No categories synced yet.</p>
      )}
      {categories.map((c) => (
        <button
          key={c.id}
          type="button"
          onClick={() => onSelect(c.id)}
          className={`w-full text-left px-3 py-2 text-sm border-b flex justify-between items-center ${
            selectedCategoryId === c.id ? "bg-black text-white" : "hover:bg-gray-50"
          }`}
        >
          <span className="truncate">{c.name}</span>
          <span className="text-xs opacity-70">{c.product_count}</span>
        </button>
      ))}
    </nav>
  );
}
