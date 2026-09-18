import type { Dispatch, MutableRefObject, SetStateAction } from "react";

/**
 * Click-to-toggle multi-select with shift-click range-select, the same interaction as a file
 * manager: a plain click toggles just that one item and becomes the new anchor; holding shift
 * while clicking adds every item between the last-clicked anchor and this one (in whatever order
 * `orderedIds` lists them) to the selection, without needing to click each one individually.
 *
 * `anchorRef` is a plain `useRef<number | null>(null)` owned by the caller - kept outside the
 * selection Set itself since the anchor is "last thing clicked", not part of what's selected.
 */
export function toggleSelection(
  id: number,
  orderedIds: number[],
  shiftKey: boolean,
  setSelected: Dispatch<SetStateAction<Set<number>>>,
  anchorRef: MutableRefObject<number | null>,
): void {
  if (shiftKey && anchorRef.current !== null) {
    const anchorIndex = orderedIds.indexOf(anchorRef.current);
    const clickedIndex = orderedIds.indexOf(id);
    if (anchorIndex !== -1 && clickedIndex !== -1) {
      const [start, end] = anchorIndex < clickedIndex ? [anchorIndex, clickedIndex] : [clickedIndex, anchorIndex];
      const rangeIds = orderedIds.slice(start, end + 1);
      setSelected((prev) => {
        const next = new Set(prev);
        for (const rid of rangeIds) next.add(rid);
        return next;
      });
      anchorRef.current = id;
      return;
    }
  }
  setSelected((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    return next;
  });
  anchorRef.current = id;
}
