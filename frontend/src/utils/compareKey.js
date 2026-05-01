/**
 * Canonical comparison key for grocery item dedup. Mirrors
 * `mealrunner.normalize.compare_key` on the backend so frontend membership
 * checks (staples picker badge, autocomplete exclude, duplicate-add warning)
 * agree with the backend's INSERT-time dedup.
 *
 * Lowercases, normalizes whitespace, depluralizes the last word. We only
 * collapse plurals — qualifier-stripping (e.g. "soy milk" → "milk") is
 * intentionally NOT done since those are different products at the store.
 *
 * NOT stored anywhere; only used for comparisons. The displayed name on
 * the row stays as typed.
 */
const VES_TO_F = {
  loaves: 'loaf', leaves: 'leaf', halves: 'half',
  calves: 'calf', shelves: 'shelf', thieves: 'thief',
  knives: 'knife', wives: 'wife', lives: 'life',
}

function depluralize(word) {
  if (VES_TO_F[word]) return VES_TO_F[word]
  if (word.endsWith('ies') && word.length > 4) return word.slice(0, -3) + 'y'
  if (word.endsWith('es') && word.length > 3) {
    const stem = word.slice(0, -2)
    if (/(sh|ch|x|z|ss|o)$/.test(stem)) return stem
    return word.slice(0, -1)
  }
  if (word.endsWith('s') && word.length > 2) return word.slice(0, -1)
  return word
}

export function compareKey(name) {
  const n = (name || '').toLowerCase().split(/\s+/).filter(Boolean)
  if (!n.length) return ''
  n[n.length - 1] = depluralize(n[n.length - 1])
  return n.join(' ')
}
