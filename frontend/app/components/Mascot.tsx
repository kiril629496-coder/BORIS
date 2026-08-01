// Mascot component for БОРИС
// Save to: frontend/app/components/Mascot.tsx

import React, { useEffect, useRef, useState } from 'react';

export type MascotMood = 'idle' | 'thinking' | 'alert' | 'curious' | 'happy';

interface MascotProps {
  size?: number;
  interactive?: boolean;
  mood?: MascotMood;
}

const MOUTHS: Record<string, string> = {
  idle:     'M2 2C6 8 18 8 22 2',
  thinking: 'M6 5H18',
  alert:    'M2 8C6 2 18 2 22 8',
  curious:  'M4 3C8 9 14 9 20 4',
  happy:    'M2 1C6 11 18 11 22 1',
};

export const Mascot: React.FC<MascotProps> = ({ size = 64, interactive = true, mood = 'idle' }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [pupilOffset, setPupilOffset] = useState({ x: 0, y: 0 });
  const [isBlinking, setIsBlinking] = useState(false);
  const [hasMouse, setHasMouse] = useState(false);

  // Check if device supports fine pointers (has a cursor)
  useEffect(() => {
    const mediaQuery = window.matchMedia('(pointer: fine)');
    setHasMouse(mediaQuery.matches);
    
    const listener = (e: MediaQueryListEvent) => {
      setHasMouse(e.matches);
    };
    mediaQuery.addEventListener('change', listener);
    return () => mediaQuery.removeEventListener('change', listener);
  }, []);

  // Eyes tracking mouse movement (Desktop only, if interactive is enabled)
  useEffect(() => {
    if (!interactive || !hasMouse) {
      setPupilOffset({ x: 0, y: 0 });
      return;
    }

    const handleMouseMove = (e: MouseEvent) => {
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;

      const deltaX = e.clientX - centerX;
      const deltaY = e.clientY - centerY;
      const distance = Math.sqrt(deltaX * deltaX + deltaY * deltaY);

      // Max pupil offset of 3px
      const maxOffset = 3.5;
      let shiftX = 0;
      let shiftY = 0;

      if (distance > 0) {
        // Smooth scaling or limiting
        const limitFactor = Math.min(distance / 150, 1);
        shiftX = (deltaX / distance) * maxOffset * limitFactor;
        shiftY = (deltaY / distance) * maxOffset * limitFactor;
      }

      // Quick smooth easing
      setPupilOffset(prev => ({
        x: prev.x + (shiftX - prev.x) * 0.15,
        y: prev.y + (shiftY - prev.y) * 0.15,
      }));
    };

    window.addEventListener('mousemove', handleMouseMove);
    return () => window.removeEventListener('mousemove', handleMouseMove);
  }, [interactive, hasMouse]);

  // Periodic blinking logic (every 4 to 6 seconds)
  useEffect(() => {
    const blinkInterval = setInterval(() => {
      setIsBlinking(true);
      setTimeout(() => {
        setIsBlinking(false);
      }, 150); // Blink duration
    }, 4000 + Math.random() * 2000);

    return () => clearInterval(blinkInterval);
  }, []);

  return (
    <div
      ref={containerRef}
      className="relative select-none flex items-center justify-center"
      style={{ width: size, height: size * 1.15 }}
    >
      {/* Mascot Container */}
      <div className="relative flex flex-col items-center w-full h-full justify-end">
        {/* Antenna */}
        <div 
          className="w-1.5 rounded-full bg-blue-600 animate-pulse" 
          style={{ 
            height: size * 0.18, 
            marginBottom: -size * 0.04, 
            zIndex: 10 
          }} 
        />
        {/* Antenna Tip */}
        <div 
          className="absolute rounded-full bg-blue-600"
          style={{
            width: size * 0.12,
            height: size * 0.12,
            top: size * 0.01,
            boxShadow: '0 0 8px rgba(47, 111, 237, 0.6)'
          }}
        />

        {/* Head Circle */}
        <div
          className="relative rounded-full bg-blue-600 border border-blue-500 flex flex-col items-center justify-center shadow-lg transition-transform hover:scale-105 duration-300"
          style={{
            width: size,
            height: size,
            boxShadow: '0 10px 25px -5px rgba(47, 111, 237, 0.4)'
          }}
        >
          {/* Eyes Group */}
          <div className="flex justify-between w-1/2 mb-1.5">
            {/* Left Eye */}
            <div
              className="rounded-full bg-white relative flex items-center justify-center overflow-hidden transition-all duration-100"
              style={{
                width: size * 0.22,
                height: isBlinking ? size * 0.02 : size * 0.22,
                marginTop: isBlinking ? size * 0.1 : 0
              }}
            >
              {!isBlinking && (
                <div
                  className="rounded-full bg-slate-900 absolute"
                  style={{
                    width: size * 0.12,
                    height: size * 0.12,
                    transform: `translate(${pupilOffset.x}px, ${pupilOffset.y}px)`,
                  }}
                />
              )}
            </div>

            {/* Right Eye */}
            <div
              className="rounded-full bg-white relative flex items-center justify-center overflow-hidden transition-all duration-100"
              style={{
                width: size * 0.22,
                height: isBlinking ? size * 0.02 : size * 0.22,
                marginTop: isBlinking ? size * 0.1 : 0
              }}
            >
              {!isBlinking && (
                <div
                  className="rounded-full bg-slate-900 absolute"
                  style={{
                    width: size * 0.12,
                    height: size * 0.12,
                    transform: `translate(${pupilOffset.x}px, ${pupilOffset.y}px)`,
                  }}
                />
              )}
            </div>
          </div>

          {/* Friendly Smile */}
          <svg
            className="w-1/4 text-white"
            viewBox="0 0 24 12"
            fill="none"
            stroke="currentColor"
            strokeWidth="3.5"
            strokeLinecap="round"
          >
            <path d={MOUTHS[mood] || MOUTHS.idle} />
          </svg>
        </div>
      </div>
    </div>
  );
};
