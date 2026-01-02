/**
 * Asset Takeoff Types
 *
 * Types for the Engineering Takeoff Asset Page components.
 */

export type Discipline =
  | 'ARCHITECTURAL'
  | 'ELECTRICAL'
  | 'MECHANICAL'
  | 'PLUMBING';

export type TakeoffStatus = 'NOT_STARTED' | 'IN_PROGRESS' | 'COMPLETED';

export interface AssetDemo {
  id: string;
  name: string;
  propertyName: string;
  discipline: Discipline;
  location?: string;
  description?: string;
  specSection?: string;
  status: TakeoffStatus;
}

export interface AssetTakeoffDemo {
  assetId: string;
  quantity: QuantityInfoDemo;
  classification: ClassificationInfoDemo;
  costs: CostInfoDemo;
  docs: DocumentationInfoDemo;
  references: DocumentReferenceDemo[];
}

export interface QuantityInfoDemo {
  autoDetectedQuantity?: number;
  autoDetectedUnit?: string;
  manualQuantity?: number | null;
  manualUnit?: string | null;
  drawingSnippets: DrawingSnippetDemo[];
  photos: PhotoDemo[];
}

export interface ClassificationInfoDemo {
  suggestedCode: string;
  suggestedDescription: string;
  confidence: number;
  appliedCode?: string;
  appliedDescription?: string;
  decisionSource?: 'SUGGESTED' | 'OVERRIDDEN';
  wizardAnswers: Record<string, string | boolean | null>;
  irsRuleRefs: IRSRuleReferenceDemo[];
}

export interface CostInfoDemo {
  actualTotal: number;
  estimatedTotal: number;
  currency: string;
  breakdown: CostBreakdownLineDemo[];
  historicalNotes?: string;
}

export interface DocumentationInfoDemo {
  notes: string;
  attachments: AttachmentDemo[];
  autoSummary: string;
}

export interface DrawingSnippetDemo {
  id: string;
  title: string;
  imageUrl: string;
}

export interface PhotoDemo {
  id: string;
  imageUrl: string;
  caption?: string;
}

export interface IRSRuleReferenceDemo {
  id: string;
  codeSection: string;
  title: string;
  shortExcerpt: string;
}

export interface CostBreakdownLineDemo {
  id: string;
  label: string;
  amount: number;
  editable?: boolean;
}

export interface AttachmentDemo {
  id: string;
  fileName: string;
  sizeLabel: string;
}

export interface DocumentReferenceDemo {
  id: string;
  type: 'PLAN' | 'SPEC' | 'SCHEDULE' | 'COST';
  title: string;
  description?: string;
  thumbnailUrl?: string;
  content?: string;
}

export type TabId = 'overview' | 'quantity' | 'classification' | 'costs' | 'documentation';

export interface WizardQuestion {
  id: string;
  question: string;
  type: 'boolean' | 'select';
  options?: { value: string; label: string }[];
}

