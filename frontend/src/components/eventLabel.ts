/** "operator.mark_reviewed" → "mark reviewed" (display label for audit events). */
export function eventLabel(eventType: string): string {
  const [, action = eventType] = eventType.split(".");
  return action.replace(/_/g, " ");
}
