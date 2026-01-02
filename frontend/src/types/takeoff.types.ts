/**
 * Takeoff Types
 * 
 * Note: In demo mode, we use Date instead of Firebase Timestamp
 */

export interface Takeoff {
  id: string;
  // Legacy fields (kept for backward compatibility)
  category?: string;
  description: string;
  quantity: number;
  unit?: string; // Legacy field, use unitOfMeasure instead
  unitCost?: number; // Legacy field, use takeoffCost instead
  totalCost?: number; // Legacy field, calculated from quantity * takeoffCost
  location?: string;
  room?: string;
  depreciationClass?: '5-year' | '15-year' | '27.5-year';
  irsMacrsCode?: string;
  notes?: string;
  
  // New Excel column fields
  titles?: string;
  refNumber?: string; // REF #
  contractNumber?: string; // Contract #
  propertyUnitNumber?: string; // Property Unit #
  takeoffCode?: string; // Takeoff Code
  unitOfMeasure?: string; // Unit Of Measure
  depreciationLocation2001CostAdjustmentIndirects?: string; // (Depreciation, Location & 2001 Cost Adjustment Indirects)
  propTax?: number; // Prop Tax
  insurance?: number; // Insurance
  tax?: number; // Tax
  costSource?: string; // Cost Source
  phase?: string; // Phase
  building?: string; // Building
  takeoffCost?: number; // Takeoff Cost
  landscapingCalculationForAllocationSheet?: string; // Landscaping Calculation For Allocation Sheet
  totalAdjust?: number; // Total Adjust (Linked w/ Header)
  buildingNumber?: string; // Building # (Linked w/ Header)
}

// Document structure for takeoffs subcollections
export interface TakeoffsDocument {
  takeoffs: Takeoff[];
  createdAt: Date;
  updatedAt: Date;
}
