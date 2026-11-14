// Harbor House: the synthetic sample study (web/live/sample-api.js). No value here comes from a
// real study. The rooms figure (rooms.js) reads its photos and room inventories from here; the
// inventories are the sample API's (sample-api.js INVENTORY) as the app labels them.

export const PHOTOS = {
  kitchen: { src: 'kitchen.webp', w: 1344, h: 896, room: 'kitchen' },
  'kitchen-detail': { src: 'kitchen-detail.webp', w: 1152, h: 768, room: 'kitchen' },
  living: { src: 'living.webp', w: 1152, h: 768, room: 'living' },
  bedroom: { src: 'bedroom.webp', w: 1344, h: 895, room: 'bedroom' },
  exterior: { src: 'exterior.webp', w: 1344, h: 896, room: 'exterior' },
};

// Room inventories (sample-api.js INVENTORY) as the app labels them; photos = where each is seen.
export const ROOMS = [
  { key: 'kitchen', name: 'Kitchen', area: 340, floor: '1', photos: ['kitchen', 'kitchen-detail'], inventory: [
    ['Kitchen cabinet', 4], ['Countertop', 2], ['Range', 1], ['Range hood', 1], ['Pendant light', 2], ['Bar stool', 4], ['Built-in shelving', 1], ['Sink', 1]] },
  { key: 'living', name: 'Living room', area: 460, floor: '1', photos: ['living'], inventory: [
    ['Fireplace', 1], ['Built-in shelving', 2], ['Wood wall panel', 1], ['Light fixture', 1], ['Air vent', 2], ['Glass door', 1], ['Window', 1]] },
  { key: 'bedroom', name: 'Primary bedroom', area: 280, floor: '2', photos: ['bedroom'], inventory: [
    ['Window', 2], ['Glass door', 1], ['Recessed light', 4], ['Air vent', 1], ['Smoke detector', 1]] },
  { key: 'exterior', name: 'Exterior & pool', area: null, floor: null, photos: ['exterior'], inventory: [
    ['Swimming pool', 1], ['Patio', 1], ['Lawn', 1], ['Wall light', 3], ['Security camera', 1], ['Fence', 1], ['Covered patio', 1]] },
];
