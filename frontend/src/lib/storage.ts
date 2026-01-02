/**
 * Firebase Storage Service
 *
 * Real Firebase Storage implementation for file uploads.
 */

import {
  ref,
  uploadBytesResumable,
  getDownloadURL as fbGetDownloadURL,
  deleteObject,
} from 'firebase/storage';
import { storage, isConfigured } from './firebase';

export interface StorageService {
  /**
   * Upload a file to storage
   */
  uploadFile(
    file: File,
    path: string,
    onProgress?: (progress: number) => void
  ): Promise<{ storagePath: string; downloadURL: string }>;

  /**
   * Get a download URL for a file
   */
  getDownloadURL(storagePath: string): Promise<string>;

  /**
   * Delete a file from storage
   */
  deleteFile(storagePath: string): Promise<void>;
}

/**
 * Firebase Storage Service Implementation
 */
class FirebaseStorageService implements StorageService {
  async uploadFile(
    file: File,
    path: string,
    onProgress?: (progress: number) => void
  ): Promise<{ storagePath: string; downloadURL: string }> {
    if (!isConfigured || !storage) {
      throw new Error('Firebase Storage is not configured');
    }

    return new Promise((resolve, reject) => {
      const storageRef = ref(storage, path);
      const uploadTask = uploadBytesResumable(storageRef, file);

      uploadTask.on(
        'state_changed',
        (snapshot) => {
          const progress = (snapshot.bytesTransferred / snapshot.totalBytes) * 100;
          if (onProgress) {
            onProgress(Math.round(progress));
          }
        },
        (error) => {
          console.error('Upload error:', error);
          reject(error);
        },
        async () => {
          try {
            const downloadURL = await fbGetDownloadURL(uploadTask.snapshot.ref);
            resolve({
              storagePath: path,
              downloadURL,
            });
          } catch (error) {
            reject(error);
          }
        }
      );
    });
  }

  async getDownloadURL(storagePath: string): Promise<string> {
    if (!isConfigured || !storage) {
      throw new Error('Firebase Storage is not configured');
    }

    const storageRef = ref(storage, storagePath);
    return fbGetDownloadURL(storageRef);
  }

  async deleteFile(storagePath: string): Promise<void> {
    if (!isConfigured || !storage) {
      throw new Error('Firebase Storage is not configured');
    }

    const storageRef = ref(storage, storagePath);
    await deleteObject(storageRef);
  }
}

// Export singleton instance
export const storageService: StorageService = new FirebaseStorageService();
