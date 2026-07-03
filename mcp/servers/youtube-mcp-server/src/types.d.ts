declare module 'youtube-captions-scraper' {
  interface CaptionOptions {
    videoID: string;
    lang?: string;
  }

  interface CaptionItem {
    text: string;
    duration: number;
    offset: number;
  }

  export function getSubtitles(options: CaptionOptions): Promise<CaptionItem[]>;
} 