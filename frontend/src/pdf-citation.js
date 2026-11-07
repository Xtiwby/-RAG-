// Match the indexed PDF text to its browser text layer without relying on
// either extractor's spaces or line wrapping.
const ignored = /[\s\u00ad\u200b\ufeff]/gu
const normalize = (text) => text.normalize('NFKC').replace(ignored, '')

function uniqueMatch(page, text) {
  const start = page.indexOf(text)
  return start >= 0 && page.indexOf(text, start + 1) < 0 ? start : -1
}

export function citationTextRanges(items, citation) {
  const target = normalize(citation)
  if (!target) return { ranges: [], partial: false }

  let page = ''
  const positions = []
  items.forEach((text, item) => {
    let offset = 0
    for (const char of text) {
      const normalized = normalize(char)
      page += normalized
      for (let i = 0; i < normalized.length; i++) {
        positions.push({ item, start: offset, end: offset + char.length })
      }
      offset += char.length
    }
  })

  const exact = uniqueMatch(page, target)
  const matches = []
  if (exact >= 0) {
    matches.push([exact, exact + target.length])
  } else if (!page.includes(target)) {
    // PDF extractors can disagree on reading order, especially in columns.
    // Only mark unique substantial lines, and require most of the citation
    // to match before displaying a partial highlight.
    for (const line of new Set(citation.split(/\r?\n/).map(normalize))) {
      if (line.length < 10) continue
      const start = uniqueMatch(page, line)
      if (start >= 0) matches.push([start, start + line.length])
    }
  }

  const merged = []
  for (const [start, end] of matches.sort((a, b) => a[0] - b[0])) {
    const previous = merged.at(-1)
    if (previous && start <= previous[1]) previous[1] = Math.max(previous[1], end)
    else merged.push([start, end])
  }
  const covered = merged.reduce((sum, [start, end]) => sum + end - start, 0)
  if (exact < 0 && covered < target.length * 0.65) return { ranges: [], partial: false }

  const ranges = []
  for (const [start, end] of merged) {
    for (let i = start; i < end; i++) {
      const position = positions[i]
      const previous = i > start ? ranges.at(-1) : null
      if (previous?.item === position.item) previous.end = position.end
      else ranges.push({ ...position })
    }
  }
  return { ranges, partial: exact < 0 }
}
