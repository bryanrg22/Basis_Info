/**
 * Classification Wizard Constants
 *
 * IRS classification wizard questions for asset categorization.
 * These are used in the Engineering Takeoff workflow.
 */

import { WizardQuestion } from '@/types/asset-takeoff.types';

/**
 * Wizard questions for IRS asset classification.
 * These questions help determine the correct depreciation category.
 */
export const WIZARD_QUESTIONS: WizardQuestion[] = [
  {
    id: 'is-building-system',
    question: "Is this asset part of the building's core systems (HVAC, electrical, plumbing)?",
    type: 'boolean',
  },
  {
    id: 'serves-specific-equipment',
    question: 'Does this asset serve specific equipment or processes rather than general building needs?',
    type: 'boolean',
  },
  {
    id: 'removal-damages-building',
    question: 'Would removing this asset cause damage to the building structure?',
    type: 'boolean',
  },
  {
    id: 'is-movable',
    question: 'Can this asset be relocated without significant modification?',
    type: 'boolean',
  },
  {
    id: 'asset-life-category',
    question: 'What is the expected useful life category?',
    type: 'select',
    options: [
      { value: '5-year', label: '5-year (Office equipment, some fixtures)' },
      { value: '7-year', label: '7-year (Office furniture, partitions)' },
      { value: '15-year', label: '15-year (Building systems, land improvements)' },
      { value: '27.5-year', label: '27.5-year (Residential rental property)' },
    ],
  },
];
