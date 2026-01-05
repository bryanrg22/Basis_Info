/**
 * Mock Storage Service Implementation
 * 
 * Simulates file uploads with deterministic progress.
 * Files are stored in data/uploads/ and served via API routes.
 */

import { StorageService } from './storage';

class MockStorageService implements StorageService {
  private mockFiles: Map<string, { url: string; file: File }> = new Map();

  async uploadFile(
    file: File,
    path: string,
    onProgress?: (progress: number) => void
  ): Promise<{ storagePath: string; downloadURL: string }> {
    // Simulate upload progress with deterministic steps: 0% → 10% → 20% → ... → 100%
    return new Promise((resolve, reject) => {
      let progress = 0;
      const interval = setInterval(() => {
        progress += 10; // 10% increments
        if (onProgress) {
          onProgress(progress);
        }
        
        if (progress >= 100) {
          clearInterval(interval);
          
          // Actually upload file via API route
          const formData = new FormData();
          formData.append('file', file);
          formData.append('path', path);
          
          fetch('/api/files/upload', {
            method: 'POST',
            body: formData,
          })
            .then(response => response.json())
            .then(data => {
              // Store mock file reference
              this.mockFiles.set(path, { url: data.downloadURL, file });
              
              resolve({
                storagePath: path,
                downloadURL: data.downloadURL,
              });
            })
            .catch(error => {
              console.error('Error uploading file:', error);
              // Fallback to mock URL if upload fails
              const downloadURL = this.generateDownloadURL(path);
              this.mockFiles.set(path, { url: downloadURL, file });
              resolve({
                storagePath: path,
                downloadURL,
              });
            });
        }
      }, 100); // 100ms intervals
    });
  }

  async getDownloadURL(storagePath: string): Promise<string> {
    const mockFile = this.mockFiles.get(storagePath);
    if (mockFile) {
      return mockFile.url;
    }
    
    // Generate a new download URL if file doesn't exist in our mock storage
    return this.generateDownloadURL(storagePath);
  }

  async deleteFile(storagePath: string): Promise<void> {
    this.mockFiles.delete(storagePath);
    // Simulate async operation
    return Promise.resolve();
  }

  private generateDownloadURL(storagePath: string): string {
    // Generate URL pointing to API route for file serving
    // Encode the path for URL
    const encodedPath = encodeURIComponent(storagePath);
    return `/api/files/${encodedPath}`;
  }
}

// Export singleton instance
export const storageService: StorageService = new MockStorageService();

