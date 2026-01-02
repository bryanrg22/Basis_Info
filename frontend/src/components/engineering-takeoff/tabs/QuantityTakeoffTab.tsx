'use client';

/**
 * Quantity Takeoff Tab
 * 
 * Tab for managing quantity data, viewing drawings and photos.
 */

import { useState } from 'react';
import { AssetDemo, AssetTakeoffDemo, QuantityInfoDemo, DrawingSnippetDemo, PhotoDemo } from '@/types/asset-takeoff.types';

interface QuantityTakeoffTabProps {
  asset: AssetDemo;
  takeoff: AssetTakeoffDemo;
  onUpdateTakeoff: (updates: Partial<AssetTakeoffDemo>) => void;
}

export function QuantityTakeoffTab({ asset, takeoff, onUpdateTakeoff }: QuantityTakeoffTabProps) {
  const [manualQuantity, setManualQuantity] = useState<string>(
    takeoff.quantity.manualQuantity?.toString() ?? ''
  );
  const [manualUnit, setManualUnit] = useState<string>(
    takeoff.quantity.manualUnit ?? takeoff.quantity.autoDetectedUnit ?? ''
  );
  const [selectedImage, setSelectedImage] = useState<{ type: 'snippet' | 'photo'; item: DrawingSnippetDemo | PhotoDemo } | null>(null);
  
  const handleSave = () => {
    const newQuantity: QuantityInfoDemo = {
      ...takeoff.quantity,
      manualQuantity: manualQuantity ? parseFloat(manualQuantity) : null,
      manualUnit: manualUnit || null,
    };
    onUpdateTakeoff({ quantity: newQuantity });
  };
  
  const handleQuantityChange = (value: string) => {
    setManualQuantity(value);
    // Debounced save would go here in real implementation
  };
  
  const handleQuantityBlur = () => {
    handleSave();
  };
  
  const displayQuantity = manualQuantity || takeoff.quantity.autoDetectedQuantity?.toString() || '—';
  const displayUnit = manualUnit || takeoff.quantity.autoDetectedUnit || '';
  const isOverridden = manualQuantity !== '' && manualQuantity !== takeoff.quantity.autoDetectedQuantity?.toString();
  
  return (
    <div className="p-6">
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Left: Quantity Form */}
        <div className="space-y-6">
          {/* Auto-detected Quantity */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Detected Quantity</h3>
            
            <div className="bg-gray-50 rounded-lg p-4 mb-4">
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-bold text-gray-900">
                  {takeoff.quantity.autoDetectedQuantity ?? '—'}
                </span>
                <span className="text-lg text-gray-500">{takeoff.quantity.autoDetectedUnit}</span>
              </div>
              <p className="text-sm text-gray-500 mt-2">
                Auto-detected from drawing analysis
              </p>
            </div>
            
            {/* Manual Override */}
            <div className="border-t border-gray-200 pt-4">
              <div className="flex items-center justify-between mb-3">
                <h4 className="font-medium text-gray-900">Manual Override</h4>
                {isOverridden && (
                  <span className="px-2 py-0.5 bg-amber-100 text-amber-700 text-xs font-medium rounded-full">
                    Override Active
                  </span>
                )}
              </div>
              
              <div className="grid grid-cols-2 gap-3 mb-3">
                <div>
                  <label className="block text-sm text-gray-600 mb-1">Quantity</label>
                  <input
                    type="number"
                    value={manualQuantity}
                    onChange={(e) => handleQuantityChange(e.target.value)}
                    onBlur={handleQuantityBlur}
                    placeholder={takeoff.quantity.autoDetectedQuantity?.toString() ?? 'Enter quantity'}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                  />
                </div>
                <div>
                  <label className="block text-sm text-gray-600 mb-1">Unit</label>
                  <select
                    value={manualUnit}
                    onChange={(e) => {
                      setManualUnit(e.target.value);
                      handleSave();
                    }}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary-500 focus:border-transparent"
                  >
                    <option value="EA">EA (Each)</option>
                    <option value="SF">SF (Square Feet)</option>
                    <option value="LF">LF (Linear Feet)</option>
                    <option value="CY">CY (Cubic Yards)</option>
                    <option value="GAL">GAL (Gallons)</option>
                    <option value="TON">TON</option>
                  </select>
                </div>
              </div>
              
              {isOverridden && (
                <button
                  onClick={() => {
                    setManualQuantity('');
                    onUpdateTakeoff({
                      quantity: {
                        ...takeoff.quantity,
                        manualQuantity: null,
                      },
                    });
                  }}
                  className="mt-3 text-sm text-red-600 hover:text-red-700"
                >
                  Clear Override
                </button>
              )}
            </div>
          </div>
          
          {/* Summary */}
          <div className="bg-primary-50 rounded-xl border border-primary-200 p-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-full bg-primary-100 flex items-center justify-center">
                <svg className="h-5 w-5 text-primary-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                </svg>
              </div>
              <div>
                <p className="text-sm text-primary-700">Final Quantity</p>
                <p className="text-xl font-bold text-primary-900">
                  {displayQuantity} {displayUnit}
                </p>
              </div>
            </div>
          </div>
        </div>
        
        {/* Right: Drawings and Photos */}
        <div className="space-y-6">
          {/* Drawing Snippets */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Drawing Snippets</h3>
            
            {takeoff.quantity.drawingSnippets.length > 0 ? (
              <div className="grid grid-cols-2 gap-3">
                {takeoff.quantity.drawingSnippets.map((snippet) => (
                  <button
                    key={snippet.id}
                    onClick={() => setSelectedImage({ type: 'snippet', item: snippet })}
                    className="group relative aspect-video rounded-lg overflow-hidden border border-gray-200 hover:border-primary-300 transition-colors"
                  >
                    <img
                      src={snippet.imageUrl}
                      alt={snippet.title}
                      className="w-full h-full object-cover"
                    />
                    <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
                    <div className="absolute bottom-0 left-0 right-0 p-2 opacity-0 group-hover:opacity-100 transition-opacity">
                      <p className="text-xs text-white font-medium truncate">{snippet.title}</p>
                    </div>
                    <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
                      <span className="px-2 py-1 bg-white/90 rounded text-xs font-medium text-gray-700">
                        View
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="text-center py-8 text-gray-500">
                <svg className="h-12 w-12 mx-auto text-gray-300 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <p className="text-sm">No drawing snippets available</p>
              </div>
            )}
          </div>
          
          {/* Field Photos */}
          <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6">
            <h3 className="text-lg font-semibold text-gray-900 mb-4">Field Photos</h3>
            
            {takeoff.quantity.photos.length > 0 ? (
              <div className="grid grid-cols-2 gap-3">
                {takeoff.quantity.photos.map((photo) => (
                  <button
                    key={photo.id}
                    onClick={() => setSelectedImage({ type: 'photo', item: photo })}
                    className="group relative aspect-square rounded-lg overflow-hidden border border-gray-200 hover:border-primary-300 transition-colors"
                  >
                    <img
                      src={photo.imageUrl}
                      alt={photo.caption || 'Field photo'}
                      className="w-full h-full object-cover"
                    />
                    <div className="absolute inset-0 bg-gradient-to-t from-black/60 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
                    {photo.caption && (
                      <div className="absolute bottom-0 left-0 right-0 p-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        <p className="text-xs text-white font-medium truncate">{photo.caption}</p>
                      </div>
                    )}
                    <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
                      <span className="px-2 py-1 bg-white/90 rounded text-xs font-medium text-gray-700">
                        View
                      </span>
                    </div>
                  </button>
                ))}
              </div>
            ) : (
              <div className="text-center py-8 text-gray-500">
                <svg className="h-12 w-12 mx-auto text-gray-300 mb-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
                <p className="text-sm">No field photos available</p>
              </div>
            )}
          </div>
        </div>
      </div>
      
      {/* Image Modal */}
      {selectedImage && (
        <ImageModal
          image={selectedImage}
          onClose={() => setSelectedImage(null)}
        />
      )}
    </div>
  );
}

interface ImageModalProps {
  image: { type: 'snippet' | 'photo'; item: DrawingSnippetDemo | PhotoDemo };
  onClose: () => void;
}

function ImageModal({ image, onClose }: ImageModalProps) {
  const title = image.type === 'snippet' 
    ? (image.item as DrawingSnippetDemo).title 
    : (image.item as PhotoDemo).caption || 'Field Photo';
  
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70" onClick={onClose} />
      <div className="relative bg-white rounded-xl shadow-2xl max-w-4xl max-h-[90vh] overflow-hidden">
        <div className="sticky top-0 bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between">
          <h3 className="font-semibold text-gray-900">{title}</h3>
          <button
            onClick={onClose}
            className="p-1 text-gray-400 hover:text-gray-600 transition-colors"
          >
            <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        <div className="p-4 overflow-auto max-h-[calc(90vh-60px)]">
          <img
            src={image.item.imageUrl}
            alt={title}
            className="w-full h-auto rounded-lg"
          />
        </div>
      </div>
    </div>
  );
}

