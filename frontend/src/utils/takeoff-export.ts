/**
 * Takeoff Export Utilities
 * 
 * Functions for exporting takeoffs to various formats.
 */

import { Takeoff } from '@/types';

/**
 * Export takeoffs to CSV format
 */
export function exportTakeoffsToCSV(takeoffs: Takeoff[]): string {
  if (takeoffs.length === 0) {
    return '';
  }

  // Get all unique keys from all takeoffs
  const allKeys = new Set<string>();
  takeoffs.forEach((takeoff) => {
    Object.keys(takeoff).forEach((key) => allKeys.add(key));
  });

  const headers = Array.from(allKeys);

  // Create CSV rows
  const rows = [
    headers.join(','), // Header row
    ...takeoffs.map((takeoff) =>
      headers
        .map((key) => {
          const value = (takeoff as unknown as Record<string, unknown>)[key];
          // Escape commas and quotes in CSV
          const stringValue = value != null ? String(value) : '';
          if (stringValue.includes(',') || stringValue.includes('"') || stringValue.includes('\n')) {
            return `"${stringValue.replace(/"/g, '""')}"`;
          }
          return stringValue;
        })
        .join(',')
    ),
  ];

  return rows.join('\n');
}

/**
 * Export takeoffs to JSON format
 */
export function exportTakeoffsToJSON(takeoffs: Takeoff[]): string {
  return JSON.stringify(takeoffs, null, 2);
}

/**
 * Download data as a file
 */
export function downloadFile(content: string, filename: string, mimeType: string): void {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/**
 * Export takeoffs to CSV and download
 */
export function downloadTakeoffsCSV(takeoffs: Takeoff[], filename: string = 'takeoffs.csv'): void {
  const csv = exportTakeoffsToCSV(takeoffs);
  downloadFile(csv, filename, 'text/csv');
}

/**
 * Export takeoffs to JSON and download
 */
export function downloadTakeoffsJSON(takeoffs: Takeoff[], filename: string = 'takeoffs.json'): void {
  const json = exportTakeoffsToJSON(takeoffs);
  downloadFile(json, filename, 'application/json');
}

