/** Deep link: `?why=1&candidate=<id>` opens the panel on that candidate. */
export function readWhyParams(search: string): { open: boolean; candidate: string | null } {
  const params = new URLSearchParams(search);
  const open = params.get("why") === "1";
  return { open, candidate: open ? params.get("candidate") : null };
}
