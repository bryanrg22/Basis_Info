/**
 * Takeoffs Table Component
 * 
 * Displays takeoffs in an editable table format.
 */

'use client';

import { Takeoff } from '@/types';
import { TakeoffColumn } from '@/utils/takeoff-columns';
import { formatCurrency } from '@/utils/formatting';
import { getFieldValue } from '@/utils/validation';

interface TakeoffsTableProps {
  takeoffs: Takeoff[];
  columns: TakeoffColumn[];
  visibleColumns: Set<string>;
  editingTakeoffId: string | null;
  editingField: string | null;
  onEditStart: (takeoffId: string, field: string) => void;
  onEditEnd: () => void;
  onUpdate: (takeoffId: string, field: string, value: unknown) => void;
  onDelete: (takeoffId: string) => void;
  onSelect: (takeoffId: string) => void;
  selectedTakeoffId: string | null;
  sortConfig: { field: string; direction: 'asc' | 'desc' } | null;
  onSort: (field: string) => void;
}

/**
 * Takeoffs table with inline editing
 */
export default function TakeoffsTable({
  takeoffs,
  columns,
  visibleColumns,
  editingTakeoffId,
  editingField,
  onEditStart,
  onEditEnd,
  onUpdate,
  onDelete,
  onSelect,
  selectedTakeoffId,
  sortConfig,
  onSort,
}: TakeoffsTableProps) {
  const visibleColumnDefs = columns.filter((col) => visibleColumns.has(col.key));

  const handleCellClick = (takeoffId: string, field: string) => {
    onEditStart(takeoffId, field);
  };

  const handleCellChange = (takeoffId: string, field: string, value: unknown) => {
    onUpdate(takeoffId, field, value);
    onEditEnd();
  };

  const renderCell = (takeoff: Takeoff, column: TakeoffColumn) => {
    const isEditing = editingTakeoffId === takeoff.id && editingField === column.key;
    const value = getFieldValue(takeoff as unknown as Record<string, unknown>, column.key);

    if (isEditing) {
      if (column.type === 'number') {
        return (
          <input
            type="number"
            step="0.01"
            defaultValue={value as number}
            onBlur={(e) => {
              const numValue = parseFloat(e.target.value) || 0;
              handleCellChange(takeoff.id, column.key, numValue);
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                const numValue = parseFloat((e.target as HTMLInputElement).value) || 0;
                handleCellChange(takeoff.id, column.key, numValue);
              } else if (e.key === 'Escape') {
                onEditEnd();
              }
            }}
            autoFocus
            className="w-full px-2 py-1 border border-primary-500 rounded text-sm"
          />
        );
      } else {
        return (
          <input
            type="text"
            defaultValue={value as string}
            onBlur={(e) => handleCellChange(takeoff.id, column.key, e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                handleCellChange(takeoff.id, column.key, (e.target as HTMLInputElement).value);
              } else if (e.key === 'Escape') {
                onEditEnd();
              }
            }}
            autoFocus
            className="w-full px-2 py-1 border border-primary-500 rounded text-sm"
          />
        );
      }
    }

    // Display value
    if (column.type === 'number' && typeof value === 'number') {
      // Format currency for cost fields
      if (column.key.includes('Cost') || column.key.includes('Tax') || column.key.includes('Total')) {
        return formatCurrency(value);
      }
      return value.toLocaleString();
    }

    return <span>{value != null ? String(value) : ''}</span>;
  };

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y divide-gray-200">
        <thead className="bg-gray-50">
          <tr>
            {visibleColumnDefs.map((column) => (
              <th
                key={column.key}
                className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider cursor-pointer hover:bg-gray-100"
                onClick={() => onSort(column.key)}
              >
                <div className="flex items-center gap-2">
                  {column.label}
                  {sortConfig?.field === column.key && (
                    <span>{sortConfig.direction === 'asc' ? '↑' : '↓'}</span>
                  )}
                </div>
              </th>
            ))}
            <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
              Actions
            </th>
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-gray-200">
          {takeoffs.map((takeoff) => (
            <tr
              key={takeoff.id}
              className={`hover:bg-gray-50 cursor-pointer ${
                selectedTakeoffId === takeoff.id ? 'bg-primary-50' : ''
              }`}
              onClick={() => onSelect(takeoff.id)}
            >
              {visibleColumnDefs.map((column) => (
                <td
                  key={column.key}
                  className="px-4 py-3 whitespace-nowrap text-sm text-gray-900"
                  onClick={(e) => {
                    e.stopPropagation();
                    handleCellClick(takeoff.id, column.key);
                  }}
                >
                  {renderCell(takeoff, column)}
                </td>
              ))}
              <td
                className="px-4 py-3 whitespace-nowrap text-sm"
                onClick={(e) => e.stopPropagation()}
              >
                <button
                  onClick={() => onDelete(takeoff.id)}
                  className="text-red-600 hover:text-red-800"
                >
                  Delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

