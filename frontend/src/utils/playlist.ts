/** A batch still doing (or waiting to do) work, as opposed to finished or needing attention. */
export const isPlaylistBatchActive = (status: string) => ['running', 'pending', 'paused'].includes(status);
