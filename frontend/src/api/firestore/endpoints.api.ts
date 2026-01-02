/**
 * Endpoints API
 *
 * Returns backend API endpoint URLs.
 */

export const endpointsApi = {
  /**
   * Get the backend API base URL
   */
  async getRoomClassificationEndpoint(): Promise<string> {
    return process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';
  },
};
