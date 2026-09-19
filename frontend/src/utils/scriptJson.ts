/** JSON safe to interpolate into an inline <script> block.
 *
 *  An HTML parser ends a script element at the first `</script`, wherever it
 *  appears — including inside a JSON string — so a value carrying that text
 *  (a symbol, an LLM-written label) would break out of the script and inject
 *  markup. Escaping the angle brackets and the two line separators JS treats
 *  as newlines keeps the payload valid JSON while making that impossible.
 */
export function scriptJson(value: unknown): string {
  return JSON.stringify(value)
    .replace(/</g, "\\u003c")
    .replace(/>/g, "\\u003e")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");
}
