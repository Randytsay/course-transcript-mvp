type ReviewerYouTubePlayer = {
  getCurrentTime: () => number;
  getPlayerState?: () => number;
  pauseVideo?: () => void;
  playVideo?: () => void;
  seekTo: (seconds: number, allowSeekAhead: boolean) => void;
  destroy: () => void;
};

type ReviewerYouTubeNamespace = {
  Player: new (
    element: HTMLElement,
    config: {
      videoId: string;
      playerVars?: Record<string, number>;
      events?: { onReady?: (event: { target: ReviewerYouTubePlayer }) => void };
    },
  ) => ReviewerYouTubePlayer;
};

declare global {
  interface Window {
    YT?: ReviewerYouTubeNamespace;
    onYouTubeIframeAPIReady?: () => void;
  }
}

export {};
