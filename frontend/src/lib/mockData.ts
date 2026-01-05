/**
 * Mock Data Re-export
 * 
 * Re-exports mock data utilities for backward compatibility
 * with code that imports from @/lib/mockData.
 */

import { Study, Asset, Statistics, UploadedFile, Room, Takeoff } from '@/types';
import { getMockDatasetForProperty, defaultDataset } from '@/mock';

/**
 * Generate mock statistics from the default dataset
 */
export const generateMockStatistics = (): Statistics => {
  return defaultDataset.statistics;
};

/**
 * Generate mock studies for the dashboard
 */
export const generateMockStudies = (): Study[] => {
  const mockUserId = 'demo-user-id';
  
  return [
    {
      id: 'study-1',
      userId: mockUserId,
      propertyName: 'Downtown Office Complex',
      totalAssets: 2500000,
      analysisDate: '2024-01-15',
      status: 'completed',
      workflowStatus: 'completed',
      uploadedFiles: [],
      assets: [],
      createdAt: new Date('2024-01-01'),
      updatedAt: new Date('2024-01-15'),
    },
    {
      id: 'study-2',
      userId: mockUserId,
      propertyName: 'Retail Shopping Center',
      totalAssets: 1800000,
      analysisDate: '2024-01-10',
      status: 'completed',
      workflowStatus: 'completed',
      uploadedFiles: [],
      assets: [],
      createdAt: new Date('2023-12-28'),
      updatedAt: new Date('2024-01-10'),
    },
    {
      id: 'study-3',
      userId: mockUserId,
      propertyName: 'Manufacturing Facility',
      totalAssets: 3200000,
      analysisDate: '2024-01-05',
      status: 'completed',
      workflowStatus: 'completed',
      uploadedFiles: [],
      assets: [],
      createdAt: new Date('2023-12-20'),
      updatedAt: new Date('2024-01-05'),
    },
  ];
};

/**
 * Generate mock assets based on total value
 */
export const generateMockAssets = (totalValue: number): Asset[] => {
  return defaultDataset.study.assets.map(asset => ({
    ...asset,
    estimatedValue: Math.round(totalValue * (asset.percentageOfTotal / 100)),
  }));
};

/**
 * Generate rooms from uploaded files
 */
export const generateRooms = (_uploadedFiles: UploadedFile[]): Room[] => {
  // Use mock dataset rooms as template
  return defaultDataset.study.rooms;
};

/**
 * Generate takeoffs from rooms
 */
export const generateTakeoffs = (_rooms: Room[] = []): Takeoff[] => {
  return defaultDataset.study.takeoffs;
};

/**
 * Generate a new study
 */
export const generateNewStudy = (
  propertyName: string,
  uploadedFiles: UploadedFile[],
  userId: string = 'demo-user-id'
): Study => {
  const dataset = getMockDatasetForProperty(propertyName);
  const now = new Date();
  
  return {
    id: `study-${Date.now()}`,
    userId,
    propertyName,
    totalAssets: dataset.study.totalAssets,
    analysisDate: now.toISOString().split('T')[0],
    status: 'pending',
    workflowStatus: 'uploading_documents',
    uploadedFiles,
    assets: [],
    rooms: [],
    takeoffs: [],
    createdAt: now,
    updatedAt: now,
  };
};

// Re-export formatting utilities
export { formatCurrency, formatDate } from '@/utils/formatting';

