/**
 * Firebase Storage File API
 *
 * Handles file uploads to Firebase Storage with real download URLs.
 */

import { getStorage, ref, uploadBytesResumable, getDownloadURL, deleteObject } from 'firebase/storage';
import { logger } from '@/lib/logger';

/**
 * Firebase Storage File API
 */
export const fileStorageApi = {
  /**
   * Upload a file to Firebase Storage
   */
  async upload(
    file: File,
    path: string,
    onProgress?: (progress: number) => void
  ): Promise<{ storagePath: string; downloadURL: string }> {
    const storage = getStorage();
    const storageRef = ref(storage, path);

    return new Promise((resolve, reject) => {
      const uploadTask = uploadBytesResumable(storageRef, file);

      uploadTask.on(
        'state_changed',
        (snapshot) => {
          const progress = (snapshot.bytesTransferred / snapshot.totalBytes) * 100;
          if (onProgress) {
            onProgress(progress);
          }
          logger.debug('Upload progress', { path, progress: Math.round(progress) });
        },
        (error) => {
          logger.error('Upload failed', { path, error: error.message });
          reject(error);
        },
        async () => {
          try {
            const downloadURL = await getDownloadURL(uploadTask.snapshot.ref);
            logger.debug('File uploaded successfully', { path, downloadURL });
            resolve({
              storagePath: path,
              downloadURL,
            });
          } catch (error) {
            logger.error('Failed to get download URL', { path, error });
            reject(error);
          }
        }
      );
    });
  },

  /**
   * Get download URL for a file in Firebase Storage
   */
  async getDownloadURL(storagePath: string): Promise<string> {
    const storage = getStorage();
    const storageRef = ref(storage, storagePath);
    return getDownloadURL(storageRef);
  },

  /**
   * Delete a file from Firebase Storage
   */
  async delete(storagePath: string): Promise<void> {
    const storage = getStorage();
    const storageRef = ref(storage, storagePath);
    await deleteObject(storageRef);
    logger.debug('File deleted', { storagePath });
  },
};
