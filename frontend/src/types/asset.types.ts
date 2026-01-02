/**
 * Asset Types
 */

export interface Asset {
  id: string;
  name: string;
  description: string;
  category: '5-year' | '15-year' | '27.5-year';
  estimatedValue: number;
  depreciationPeriod: number;
  percentageOfTotal: number;
  verified: boolean;
}

