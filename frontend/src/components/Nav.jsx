export default function Nav({ page, setPage }) {
  const link = (name, label) => (
    <a
      href="#"
      className={page === name ? 'active' : ''}
      onClick={(e) => { e.preventDefault(); setPage(name) }}
    >
      {label}
    </a>
  )

  return (
    <nav className="top-nav">
      <a href="#" className="logo" onClick={(e) => { e.preventDefault(); setPage('plan') }}>
        sous<em>chef</em>
      </a>
      <div className="nav-links">
        {link('plan', 'Plan')}
        {link('grocery', 'Grocery')}
        {link('order', 'Order')}
        {link('receipt', 'Receipt')}
      </div>
    </nav>
  )
}
