import "./style.css"

function SidePanel() {
  return (
    <div className="container">
      <header className="header">
        <h1>Threadly</h1>
        <p>Your email assistant</p>
      </header>

      <main className="content">
        <h2>Welcome to Threadly</h2>

        <p>
          Your email assistant will appear here.
        </p>

        <button>
          Please login to continue
        </button>
      </main>
    </div>
  )
}

export default SidePanel