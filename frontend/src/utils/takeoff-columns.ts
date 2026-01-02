/**
 * Takeoff Column Definitions
 * 
 * Centralized column definitions for takeoff tables.
 */

export interface TakeoffColumn {
  key: string;
  label: string;
  type: 'string' | 'number';
  required?: boolean;
}

/**
 * All available columns for takeoffs
 */
export const TAKEOFF_COLUMNS: TakeoffColumn[] = [
  { key: 'titles', label: 'Titles', type: 'string' },
  { key: 'refNumber', label: 'REF #', type: 'string' },
  { key: 'contractNumber', label: 'Contract #', type: 'string' },
  { key: 'propertyUnitNumber', label: 'Property Unit #', type: 'string' },
  { key: 'takeoffCode', label: 'Takeoff Code', type: 'string' },
  { key: 'description', label: 'Description', type: 'string', required: true },
  { key: 'quantity', label: 'Quantity', type: 'number', required: true },
  { key: 'unitOfMeasure', label: 'Unit Of Measure', type: 'string' },
  { key: 'depreciationLocation2001CostAdjustmentIndirects', label: 'Depreciation, Location & 2001 Cost Adjustment Indirects', type: 'string' },
  { key: 'propTax', label: 'Prop Tax', type: 'number' },
  { key: 'insurance', label: 'Insurance', type: 'number' },
  { key: 'tax', label: 'Tax', type: 'number' },
  { key: 'costSource', label: 'Cost Source', type: 'string' },
  { key: 'phase', label: 'Phase', type: 'string' },
  { key: 'building', label: 'Building', type: 'string' },
  { key: 'takeoffCost', label: 'Takeoff Cost', type: 'number' },
  { key: 'landscapingCalculationForAllocationSheet', label: 'Landscaping Calculation For Allocation Sheet', type: 'string' },
  { key: 'totalAdjust', label: 'Total Adjust', type: 'number' },
  { key: 'buildingNumber', label: 'Building #', type: 'string' },
  // Legacy columns for backward compatibility
  { key: 'category', label: 'Category', type: 'string' },
  { key: 'room', label: 'Room', type: 'string' },
  { key: 'location', label: 'Location', type: 'string' },
  { key: 'unitCost', label: 'Unit Cost', type: 'number' },
  { key: 'totalCost', label: 'Total Cost', type: 'number' },
  { key: 'irsMacrsCode', label: 'IRS MACRS Code', type: 'string' },
  { key: 'notes', label: 'Notes', type: 'string' },
];

/**
 * Default visible columns
 */
export const DEFAULT_VISIBLE_COLUMNS = new Set([
  'description',
  'quantity',
  'unitOfMeasure',
  'takeoffCost',
  'category',
  'room',
  'location',
]);

/**
 * IRS MACRS Code Reference
 */
export const IRS_CODES = [
  { code: '57.0', description: 'Personal Property', depreciation: '5-year' },
  { code: '57.1', description: 'Land Improvements', depreciation: '15-year' },
  { code: '1250', description: 'Building (Residential)', depreciation: '27.5-year' },
] as const;

