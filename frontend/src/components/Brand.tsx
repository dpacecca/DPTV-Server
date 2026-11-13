import logo from "../logo.svg";

/** DPTV-Server's lockup, as DPTV-Player and DPTV Recorder show theirs: the tile, then "DPTV SERVER". */
export default function Brand({ large = false }: { large?: boolean }) {
  return (
    <div className={large ? "dptv-brand large" : "dptv-brand"}>
      <img src={logo} alt="" />
      <span className="word">
        DPTV <span>Server</span>
      </span>
    </div>
  );
}
