export default function Panel({ title, children, className = '', style }) {
  return (
    <div className={`panel ${className}`} style={style}>
      {title && <div className="panel-title">{title}</div>}
      <div className="panel-body">{children}</div>
    </div>
  )
}
