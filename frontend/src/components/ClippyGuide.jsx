import { useState, useEffect } from 'react'
import clippyImg from '../assets/clippy-chef.png'
import mouseImg from '../assets/mouse-chef.png'

export default function ClippyGuide({ quip, showMouse }) {
  const [mouseVisible, setMouseVisible] = useState(false)
  const [mouseRunning, setMouseRunning] = useState(false)

  useEffect(() => {
    if (showMouse) {
      const t1 = setTimeout(() => setMouseVisible(true), 500)
      const t2 = setTimeout(() => setMouseRunning(true), 1800)
      return () => { clearTimeout(t1); clearTimeout(t2) }
    }
  }, [showMouse])

  return (
    <div className="clippy-container">
      {quip && (
        <div className="clippy-bubble">
          <span>{quip}</span>
          <div className="clippy-bubble-tail" />
        </div>
      )}
      <div className="clippy-character">
        <img
          src={clippyImg}
          alt="Clippy the chef"
          className={`clippy-img${showMouse ? ' clippy-wave' : ''}`}
        />
        {mouseVisible && (
          <img
            src={mouseImg}
            alt=""
            className={`mouse-img${mouseRunning ? ' mouse-scurry' : ''}`}
          />
        )}
      </div>
    </div>
  )
}
