const mediaExtensions = new Set(['mp4', 'mov', 'mkv', 'webm', 'avi', 'mp3', 'm4a', 'wav', 'ogg', 'flac', 'aac'])

export function isMediaDocument(document) {
  return mediaExtensions.has(document?.file_type?.toLowerCase())
}

export function mediaStartSeconds(section = '') {
  // Transcripts use HH:MM:SS ranges; sampled frame OCR uses 视频画面 HH:MM:SS.
  const match = /^(?:视频画面\s+)?(\d{2,}):([0-5]\d):([0-5]\d(?:\.\d+)?)(?=\s|$|[–—-])/.exec(section)
  return match ? Number(match[1]) * 3600 + Number(match[2]) * 60 + Number(match[3]) : null
}
