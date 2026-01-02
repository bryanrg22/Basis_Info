/**
 * Appraisal Resource Types
 *
 * These types model the key information we surface on the
 * Resource Extraction step using a simplified view of the
 * Uniform Residential Appraisal Report (URAR) JSON.
 *
 * The goal is to provide enough structure for a rich,
 * checklist-driven UI without needing to model every field
 * in the URAR spec.
 */

export interface SubjectInfo {
  form: string;
  appraisal_company: string;
  appraiser_phone: string;
  file_number: string;
  internal_id: string;
  property_address: string;
  city: string;
  state: string;
  zip: string;
  borrower: string;
  owner_of_public_record: string;
  county: string;
  legal_description: string;
  assessors_parcel_numbers: string[];
  tax_year: number;
  real_estate_taxes: number;
  neighborhood_name: string;
  map_reference: string;
  census_tract: string;
  property_rights_appraised: string;
  assignment_type: string;
  lender_client: string;
}

export interface ListingAndContractInfo {
  mls_number: string;
  days_on_market: number;
  listing_date: string;
  original_list_price: number;
  listing_expiration_date: string;
  contract_price: number;
  contract_date: string;
  sale_type: string;
  contract_documents_reviewed: string[];
  contract_provided_by: string;
  financial_assistance_concessions: number;
  subject_offered_for_sale_prior_12_months: boolean;
}

export interface NeighborhoodStatsRange {
  count: number;
  price_range_low: number;
  price_range_high: number;
}

export interface NeighborhoodInfo {
  location: string;
  built_up: string;
  growth: string;
  one_unit_value_trend: string;
  demand_supply: string;
  typical_marketing_time: string;
  one_unit_listings: NeighborhoodStatsRange;
  one_unit_sales_12_months: NeighborhoodStatsRange;
  boundaries: {
    north: string;
    south: string;
    east: string;
    west: string;
  };
  description: string;
  market_notes: string;
}

export interface SiteUtilitiesInfo {
  electric: string;
  gas: string;
  water: string;
  sanitary_sewer: string;
}

export interface SiteOffSiteImprovementsInfo {
  street: string;
  alley: string | null;
}

export interface SiteInfo {
  dimensions: string;
  area_acres: number;
  shape: string;
  view: string;
  zoning_classification: string;
  zoning_description: string;
  zoning_compliance: string;
  highest_and_best_use_as_improved: string;
  utilities: SiteUtilitiesInfo;
  off_site_improvements: SiteOffSiteImprovementsInfo;
  flood_hazard_area: boolean;
  flood_zone: string;
  fema_map_number: string;
  fema_map_date: string;
  easements_encroachments: string;
  site_comments: string;
}

export interface ImprovementsGeneralInfo {
  units: number;
  stories: number;
  type: string;
  status: string;
  design_style: string;
  year_built: number;
  effective_age_years: number;
  foundation_type: string;
  basement_area_sqft: number;
  basement_finish_percent: number;
  basement_access: string;
  overall_quality: string;
  overall_condition: string;
}

export interface ImprovementsInfo {
  general: ImprovementsGeneralInfo;
  // The rest of the improvements section is kept as a loose
  // structure since the UI surfaces it mostly as read-only
  // narrative and key-value groups.
  exterior: Record<string, unknown>;
  interior_mechanical: Record<string, unknown>;
}

export interface PriorSaleRecord {
  sale_date: string;
  sale_price: number;
  arms_length: boolean;
  notes: string;
}

export interface PriorSaleHistory {
  subject_sales: PriorSaleRecord[];
}

export interface ComparableRoomCount {
  total_rooms: number;
  bedrooms: number;
  bathrooms: number;
}

export interface ComparableBasementInfo {
  area_sqft: number;
  finished_sqft: number;
  rooms_below_grade: string | null;
  type: string;
}

export interface SalesComparable {
  id: number;
  address: string;
  city: string;
  state: string;
  proximity: string;
  sale_price: number;
  price_per_sqft: number;
  data_source: string;
  sale_type: string;
  financing: string;
  concessions: number;
  contract_date: string;
  sale_date: string;
  location: string;
  property_rights: string;
  site_area_sqft?: number;
  site_area_acres?: number;
  view: string;
  design: string;
  quality: string;
  actual_age_years: number;
  condition: string;
  room_count: ComparableRoomCount;
  gross_living_area_sqft: number;
  basement: ComparableBasementInfo;
  functional_utility: string;
  heating_cooling: string;
  energy_features: string | null;
  garage_carport: string | null;
  porch_patio_deck: string;
  fireplaces_woodstove: string | null;
  fence_outbuildings?: string;
  net_adjustment: number;
  gross_adjustment_percent: number;
  adjusted_sale_price: number;
  notes?: string;
}

export interface SalesComparisonMarketStats {
  active_listings_count: number;
  active_listings_price_range: {
    low: number;
    high: number;
  };
  sales_12_months_count: number;
  sales_12_months_price_range: {
    low: number;
    high: number;
  };
}

export interface SalesComparisonSubject {
  address: string;
  city: string;
  state: string;
  contract_price: number;
  price_per_sqft: number;
  location: string;
  property_rights: string;
  site_area_acres: number;
  view: string;
  design: string;
  quality: string;
  actual_age_years: number;
  condition: string;
  room_count: ComparableRoomCount;
  gross_living_area_sqft: number;
  basement: ComparableBasementInfo;
  heating_cooling: string;
  garage_carport: string;
  porch_patio_deck: string;
  fireplaces_woodstove: string;
  fence_shed_other: string | null;
  additional_features: string | null;
}

export interface SalesComparisonInfo {
  market_stats: SalesComparisonMarketStats;
  subject: SalesComparisonSubject;
  comparables: SalesComparable[];
}

export interface CostApproachImprovementsCostNew {
  dwelling_gla: {
    area_sqft: number;
    unit_cost: number;
    total_cost: number;
  };
  basement: {
    area_sqft: number;
    unit_cost: number;
    total_cost: number;
  };
  mechanicals_misc: number;
  garage_carport: {
    area_sqft: number;
    unit_cost: number;
    total_cost: number;
  };
}

export interface CostApproachInfo {
  site_value: number;
  improvements_cost_new: CostApproachImprovementsCostNew;
  total_cost_new: number;
  depreciation: number;
  depreciated_cost_of_improvements: number;
  as_is_site_improvements_value: number;
  indicated_value_by_cost_approach: number;
  effective_age_years: number;
  remaining_economic_life_years: number;
  cost_data_source: string;
  comments: string;
}

export interface ReconciliationInfo {
  indicated_value_sales_comparison: number;
  indicated_value_cost_approach: number | null;
  indicated_value_income_approach: number | null;
  final_market_value: number;
  effective_date_of_appraisal: string;
  value_condition: string;
  comments: string;
}

export interface AppraisalPhotoPage {
  page: number;
  labels: string[];
}

export interface SketchArea {
  type: string;
  level: string;
  square_feet: number;
  notes: string;
}

export interface SketchInfo {
  areas: SketchArea[];
  basement_layout: string[];
}

/**
 * Top-level object representing all extracted URAR resources
 * we surface to the engineer on the Resource Extraction step.
 */
export interface AppraisalResources {
  subject: SubjectInfo;
  listing_and_contract: ListingAndContractInfo;
  neighborhood: NeighborhoodInfo;
  site: SiteInfo;
  improvements: ImprovementsInfo;
  prior_sale_history: PriorSaleHistory;
  sales_comparison: SalesComparisonInfo;
  cost_approach: CostApproachInfo;
  reconciliation: ReconciliationInfo;
  photos: AppraisalPhotoPage[];
  sketch: SketchInfo;
}

/**
 * Checklist types for the Resource Extraction step.
 */
export type ResourceChecklistStatus = 'NOT_STARTED' | 'IN_REVIEW' | 'VERIFIED';

export type ResourceSectionId =
  | 'subject'
  | 'neighborhood'
  | 'site'
  | 'improvements'
  | 'sales_comparison'
  | 'cost_approach'
  | 'photos_sketch'
  | 'overall';

export interface ResourceChecklistItem {
  id: string;
  sectionId: ResourceSectionId;
  title: string;
  description?: string;
  status: ResourceChecklistStatus;
}


